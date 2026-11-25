"""Rubric 评测服务（竞品对标）：评分表 CRUD + 不可变版本 + 判定/校准。

边界（与 ``retrieval_eval_service`` / ``app.rag.evaluator`` 的分工）：

- ``retrieval_eval_service`` 衡量**检索**（文档级 recall@k / MRR）：输入 query
  与金标文档 id，不读候选答案；
- 本模块衡量**答案**：每个评分项是一个带权重的 yes/no 问题，输入是候选答案
  文本，输出是逐问题判定、加权得分与（有金标时的）评委校准报告。两者共享
  「组织隔离 + 运行快照」的产品形态，但判定对象、输入与指标完全不同，
  因此刻意不写进 ``retrieval_eval_run``，也不复用其 recall 语义；
- ``app.rag.evaluator`` 的 faithfulness/relevancy 是固定维度、连续分的评委；
  本模块的评分表由用户编辑并版本化，判定是离散 yes/no/unsure。

严格度重映射（strictness remap）：评委对每个问题返回 ``yes`` / ``no`` /
``unsure``。``yes``/``no`` 恒折算 1/0 分，``unsure`` 按该项的严格度折算：

- ``neutral``：0.5（不确定算半分）
- ``strict``：0.0（不确定按不通过，宁严勿松）
- ``lenient``：1.0（不确定按通过）

单项得分 = weight × 折算分；用例得分 = Σ(weight × 折算分) / Σweight；
运行总分为全部用例得分的宏平均（每条用例等权）。逐问题通过率 =
折算分为 1.0 的用例数 / 参与判定的用例数（因此 strictness 会同时影响
通过率，口径写死在这里，前端只展示）。

判定失败语义（绝不静默给 0 分）：

- 评委调用失败 → 运行 ``status=failed`` + ``error_code=JUDGE_UNAVAILABLE``；
- 评委输出不符合 JSON 契约（缺键/多键/非法判定值）→
  ``error_code=JUDGE_CONTRACT_VIOLATION``；
- 失败运行**同样落库**（不产生任何 aggregate/results 分数），端点映射为 502；
  绝不把「测量失败」呈现成「评了 0 分」。

校准（calibration）：用例可携带人工金标 ``gold_verdicts``（同问题的 yes/no/
unsure 人工判定）。有金标时输出总体一致率、逐问题一致率与 3×3 混淆矩阵
（judge × gold）；**没有任何金标对可比时 ``calibration`` 显式为 ``null``**，
绝不把「没有金标」说成「100% 准确」。

安全：所有读写按组织隔离，跨组织一律 404（不回显存在性）；评分表版本行不可
变，读取时重算内容哈希，损坏版本拒绝用于运行；审计只记 org/评分表 id/
版本号/条数/模式/状态等元数据，绝不记问题、答案或判定正文。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_failfast import create_chat_completion
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.eval_rubric import (
    RUBRIC_STRICTNESS_LEVELS,
    RUBRIC_VERSION_SOURCES,
    EvalRubric,
    EvalRubricRun,
    EvalRubricVersion,
)

# ── 上限（与 schema 的 Field 约束同值；服务层重复校验，防内部调用绕过 schema）──
MAX_ITEMS_PER_VERSION = 30
MAX_QUESTION_CHARS = 300
MAX_KEY_CHARS = 32
MAX_ITEM_WEIGHT = 10
MAX_NAME_CHARS = 100
MAX_DESCRIPTION_CHARS = 500
MAX_CHANGE_NOTE_CHARS = 200
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_VERSIONS_PER_RUBRIC = 100
MAX_RUBRICS_PER_ORG = 200
MAX_CASES_PER_RUN = 50
MAX_JUDGE_RUN_SECONDS = 90.0
"""单次 judge 运行的服务端墙钟预算（秒）。

部署链路里代理的超时不可控（demo 走 Cloudflare Tunnel 时边缘上限 100s），
与其让代理把响应切断成「后端已完成、前端报失败」的幽灵运行，不如在服务端
主动到期并以明确错误码失败。fake 模式不调用模型，不受此限制。
"""
MAX_CASE_ID_CHARS = 64
MAX_CASE_NAME_CHARS = 100
MAX_ANSWER_CHARS = 20_000

RUBRIC_STATUS_ACTIVE = "active"
RUBRIC_STATUS_ARCHIVED = "archived"

RUBRIC_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

VERDICTS = ("yes", "no", "unsure")
"""评委判定闭集：yes/no/unsure（unsure 的折算由 strictness 决定）。"""

REMAPPED_UNSURE_CREDIT: dict[str, float] = {
    "neutral": 0.5,
    "strict": 0.0,
    "lenient": 1.0,
}

RUN_MODE_JUDGE = "judge"
RUN_MODE_FAKE = "fake"
RUN_MODES = (RUN_MODE_JUDGE, RUN_MODE_FAKE)

RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"

ERROR_CODE_JUDGE_UNAVAILABLE = "JUDGE_UNAVAILABLE"
ERROR_CODE_JUDGE_CONTRACT = "JUDGE_CONTRACT_VIOLATION"

FAKE_JUDGE_MODEL = "fake"


class RubricJudgeError(RuntimeError):
    """评委判定失败（不可用或契约违规）——运行必须显式失败，绝不给 0 分。"""

    error_code = ERROR_CODE_JUDGE_UNAVAILABLE


class RubricJudgeUnavailableError(RubricJudgeError):
    """评委客户端缺失/调用异常（外部 LLM 服务不可用）。"""

    error_code = ERROR_CODE_JUDGE_UNAVAILABLE


class RubricJudgeContractError(RubricJudgeError):
    """评委输出不符合严格 JSON 契约（缺键/多键/非法判定值/非 JSON）。"""

    error_code = ERROR_CODE_JUDGE_CONTRACT


# 评委函数契约：给定一条用例与评分项列表，返回 {item_key: verdict}。
# 端点注入真实 LLM 实现；测试注入确定性替身。任何失败都必须抛 RubricJudgeError。
VerdictFn = Callable[[dict[str, Any], list[dict[str, Any]]], Awaitable[dict[str, str]]]


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


# ── 评分项规范化 ────────────────────────────────────────────────────────────


def normalize_rubric_items(
    raw_items: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
) -> list[dict[str, Any]]:
    """校验并规范化评分项列表（空列表非法：没有问题的评分表无法评分）。

    项形状：``{key, question, weight, strictness}``。key 小写字母开头、2-32 位
    （与模板 key 同一模式）；question 1-300 字符；weight 1-10 整数；
    strictness ∈ neutral/strict/lenient（缺省 neutral）。
    """
    if not isinstance(raw_items, (list, tuple)) or not raw_items:
        raise ValidationError("评分表至少需要一个评分项", field="items")
    if len(raw_items) > MAX_ITEMS_PER_VERSION:
        raise ValidationError(
            f"评分表最多 {MAX_ITEMS_PER_VERSION} 个评分项",
            field="items",
            expected=MAX_ITEMS_PER_VERSION,
            found=len(raw_items),
        )
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw_items:
        if not isinstance(entry, dict):
            raise ValidationError("评分项必须是对象", field="items")
        key = str(entry.get("key") or "").strip()
        if not RUBRIC_KEY_PATTERN.match(key):
            raise ValidationError(
                f"非法评分项 key: {key!r}（需匹配 {RUBRIC_KEY_PATTERN.pattern}）",
                field="items.key",
            )
        if len(key) > MAX_KEY_CHARS:  # 模式已限 32，防御性保留
            raise ValidationError(f"评分项 key 不能超过 {MAX_KEY_CHARS} 字符", field="items.key")
        if key in seen:
            raise ValidationError(f"评分项 key 重复: {key!r}", field="items.key")
        seen.add(key)
        question = str(entry.get("question") or "").strip()
        if not question:
            raise ValidationError(f"{key!r} 的问题不能为空", field="items.question")
        if len(question) > MAX_QUESTION_CHARS:
            raise ValidationError(
                f"{key!r} 的问题不能超过 {MAX_QUESTION_CHARS} 字符", field="items.question"
            )
        raw_weight = entry.get("weight", 1)
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, int):
            raise ValidationError(f"{key!r} 的权重必须是整数", field="items.weight")
        if raw_weight < 1 or raw_weight > MAX_ITEM_WEIGHT:
            raise ValidationError(
                f"{key!r} 的权重必须在 1..{MAX_ITEM_WEIGHT} 之间",
                field="items.weight",
                found=raw_weight,
            )
        strictness = str(entry.get("strictness") or "neutral").strip().lower()
        if strictness not in RUBRIC_STRICTNESS_LEVELS:
            raise ValidationError(
                f"{key!r} 的严格度必须是 {'/'.join(RUBRIC_STRICTNESS_LEVELS)} 之一",
                field="items.strictness",
            )
        normalized.append(
            {
                "key": key,
                "question": question,
                "weight": raw_weight,
                "strictness": strictness,
            }
        )
    return normalized


def version_items(row: EvalRubricVersion) -> list[dict[str, Any]] | None:
    """读取版本行评分项；哈希/项数不符返回 None（调用方必须拒绝使用）。"""
    payload = row.payload_json
    if not isinstance(payload, list) or not payload:
        return None
    if _digest(payload) != row.payload_sha256:
        return None
    if len(payload) != int(row.item_count or 0):
        return None
    return payload


# ── 序列化 ─────────────────────────────────────────────────────────────────


def _check_name(name: str) -> str:
    stripped = (name or "").strip()
    if not stripped:
        raise ValidationError("评分表名称不能为空", field="name")
    if len(stripped) > MAX_NAME_CHARS:
        raise ValidationError(f"评分表名称不能超过 {MAX_NAME_CHARS} 字符", field="name")
    return stripped


def _check_optional_text(value: str | None, *, field: str, limit: int) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > limit:
        raise ValidationError(f"{field} 不能超过 {limit} 字符", field=field)
    return stripped


def rubric_to_dict(row: EvalRubric) -> dict[str, Any]:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "name": row.name,
        "description": row.description,
        "status": row.status,
        "latest_version": int(row.latest_version or 0),
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
        "archived_by": row.archived_by,
        "lock_version": row.lock_version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def version_to_dict(row: EvalRubricVersion, *, include_items: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": row.id,
        "rubric_id": row.rubric_id,
        "version_no": int(row.version_no),
        "item_count": int(row.item_count),
        "payload_sha256": row.payload_sha256,
        "source": row.source,
        "change_note": row.change_note,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if include_items:
        data["items"] = row.payload_json
        data["intact"] = version_items(row) is not None
    return data


def run_summary_dict(row: EvalRubricRun) -> dict[str, Any]:
    aggregate = row.aggregate if isinstance(row.aggregate, dict) else None
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "rubric_id": row.rubric_id,
        "rubric_version_no": int(row.rubric_version_no),
        "dataset_hash": row.dataset_hash,
        "mode": row.mode,
        "status": row.status,
        "case_count": int(row.case_count),
        "judge_model": row.judge_model,
        "error_code": row.error_code,
        "score": aggregate.get("score") if aggregate else None,
        "calibration_available": bool(aggregate and aggregate.get("calibration")),
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def run_detail_dict(row: EvalRubricRun) -> dict[str, Any]:
    data = run_summary_dict(row)
    data["aggregate"] = row.aggregate
    data["results"] = row.results
    return data


# ── 评分表 CRUD ─────────────────────────────────────────────────────────────


async def get_rubric(session: AsyncSession, *, organization_id: int, rubric_id: str) -> EvalRubric:
    """组织内加载评分表；不存在/跨组织一律 404（不回显存在性）。"""
    row = await session.get(EvalRubric, rubric_id)
    if row is None or row.organization_id != organization_id:
        raise NotFoundError("评分表不存在", resource="eval_rubric", resource_id=rubric_id)
    return row


async def get_version(
    session: AsyncSession,
    *,
    organization_id: int,
    rubric: EvalRubric,
    version_no: int,
) -> EvalRubricVersion:
    row = (
        await session.execute(
            select(EvalRubricVersion).where(
                EvalRubricVersion.rubric_id == rubric.id,
                EvalRubricVersion.version_no == version_no,
                EvalRubricVersion.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            "评分表版本不存在",
            resource="eval_rubric_version",
            resource_id=f"{rubric.id}:{version_no}",
        )
    return row


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_rubrics(
    session: AsyncSession,
    *,
    organization_id: int,
    status: str | None = "active",
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    if status not in (None, RUBRIC_STATUS_ACTIVE, RUBRIC_STATUS_ARCHIVED, "all"):
        raise ValidationError(f"未知评分表状态: {status!r}", field="status")
    conditions = [EvalRubric.organization_id == organization_id]
    if status == RUBRIC_STATUS_ACTIVE:
        conditions.append(EvalRubric.status == RUBRIC_STATUS_ACTIVE)
    elif status == RUBRIC_STATUS_ARCHIVED:
        conditions.append(EvalRubric.status == RUBRIC_STATUS_ARCHIVED)
    query_text = (q or "").strip()
    if len(query_text) > MAX_NAME_CHARS:
        raise ValidationError(f"搜索关键词不能超过 {MAX_NAME_CHARS} 个字符", field="q")
    if query_text:
        conditions.append(EvalRubric.name.like(f"%{_escape_like(query_text)}%", escape="\\"))
    total = int(
        (await session.execute(select(func.count(EvalRubric.id)).where(*conditions))).scalar() or 0
    )
    rows = (
        (
            await session.execute(
                select(EvalRubric)
                .where(*conditions)
                .order_by(EvalRubric.created_at.desc(), EvalRubric.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [rubric_to_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def create_rubric(
    session: AsyncSession,
    *,
    organization_id: int,
    created_by: int,
    name: str,
    description: str | None = None,
    commit: bool = True,
) -> EvalRubric:
    """创建评分表头（尚无版本）；首版通过 ``create_version`` 写入。

    ``commit=False`` 供「表头 + 首版」同事务写入：调用方必须在同一 session
    上显式 commit，失败时整体回滚，避免留下 latest_version=0 的孤立评分表。
    """
    name = _check_name(name)
    description = _check_optional_text(
        description, field="description", limit=MAX_DESCRIPTION_CHARS
    )
    budget = int(
        (
            await session.execute(
                select(func.count(EvalRubric.id)).where(
                    EvalRubric.organization_id == organization_id,
                    EvalRubric.status == RUBRIC_STATUS_ACTIVE,
                )
            )
        ).scalar()
        or 0
    )
    if budget >= MAX_RUBRICS_PER_ORG:
        raise ValidationError(
            f"每个组织最多 {MAX_RUBRICS_PER_ORG} 个活跃评分表", field="name", found=budget
        )
    row = EvalRubric(
        id=str(uuid.uuid4()),
        organization_id=organization_id,
        name=name,
        description=description,
        status=RUBRIC_STATUS_ACTIVE,
        latest_version=0,
        created_by=created_by,
        lock_version=1,
    )
    session.add(row)
    if commit:
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ConflictError("同名评分表已存在", error_code="EVAL_RUBRIC_NAME_CONFLICT") from exc
        await session.refresh(row)
    return row


async def update_rubric(
    session: AsyncSession,
    *,
    rubric: EvalRubric,
    name: str | None = None,
    description: str | None = None,
    clear_description: bool = False,
    updated_by: int,
) -> EvalRubric:
    """更新评分表头（名称/说明）。归档表只读；名称冲突映射为 409。"""
    if rubric.status == RUBRIC_STATUS_ARCHIVED:
        raise ConflictError(
            "评分表已归档（只读）",
            error_code="EVAL_RUBRIC_ARCHIVED",
            detail={"rubric_id": rubric.id},
        )
    changed = False
    if name is not None:
        cleaned = _check_name(name)
        if cleaned != rubric.name:
            rubric.name = cleaned
            changed = True
    if clear_description:
        rubric.description = None
        changed = True
    elif description is not None:
        rubric.description = _check_optional_text(
            description, field="description", limit=MAX_DESCRIPTION_CHARS
        )
        changed = True
    if changed:
        rubric.updated_by = updated_by
        rubric.lock_version = int(rubric.lock_version or 1) + 1
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("同名评分表已存在", error_code="EVAL_RUBRIC_NAME_CONFLICT") from exc
    await session.refresh(rubric)
    return rubric


async def _next_version_no(session: AsyncSession, rubric_id: str) -> int:
    current = (
        await session.execute(
            select(func.max(EvalRubricVersion.version_no)).where(
                EvalRubricVersion.rubric_id == rubric_id
            )
        )
    ).scalar()
    return int(current or 0) + 1


async def _ensure_version_budget(session: AsyncSession, rubric_id: str) -> None:
    count = int(
        (
            await session.execute(
                select(func.count(EvalRubricVersion.id)).where(
                    EvalRubricVersion.rubric_id == rubric_id
                )
            )
        ).scalar()
        or 0
    )
    if count >= MAX_VERSIONS_PER_RUBRIC:
        raise ValidationError(
            f"单个评分表最多保留 {MAX_VERSIONS_PER_RUBRIC} 个版本",
            field="rubric_id",
            found=count,
        )


async def create_version(
    session: AsyncSession,
    *,
    rubric: EvalRubric,
    items: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    created_by: int,
    change_note: str | None = None,
    source: str = "manual",
    commit: bool = True,
) -> EvalRubricVersion:
    """新增一个不可变版本；版本号按 (rubric_id, max+1) 分配。

    并发保存同一评分表时唯一约束兜底，后到者 409（由用户重试，绝不静默覆盖）。
    """
    if rubric.status == RUBRIC_STATUS_ARCHIVED:
        raise ConflictError(
            "评分表已归档：不能再新增版本",
            error_code="EVAL_RUBRIC_ARCHIVED",
            detail={"rubric_id": rubric.id},
        )
    if source not in RUBRIC_VERSION_SOURCES:
        raise ValidationError(f"未知版本来源: {source!r}", field="source")
    normalized = normalize_rubric_items(items)
    if len(_canonical_json(normalized).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValidationError("评分表版本内容超过大小上限", field="items")
    change_note = _check_optional_text(
        change_note, field="change_note", limit=MAX_CHANGE_NOTE_CHARS
    )
    await _ensure_version_budget(session, rubric.id)
    version_no = await _next_version_no(session, rubric.id)
    row = EvalRubricVersion(
        id=str(uuid.uuid4()),
        rubric_id=rubric.id,
        organization_id=rubric.organization_id,
        version_no=version_no,
        payload_json=normalized,
        payload_sha256=_digest(normalized),
        item_count=len(normalized),
        source=source,
        change_note=change_note,
        created_by=created_by,
    )
    session.add(row)
    rubric.latest_version = version_no
    rubric.updated_by = created_by
    rubric.lock_version = int(rubric.lock_version or 1) + 1
    if commit:
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ConflictError(
                "评分表版本并发冲突，请重试", error_code="EVAL_RUBRIC_VERSION_CONFLICT"
            ) from exc
        await session.refresh(row)
        await session.refresh(rubric)
    return row


# 服务端默认评分表（bootstrap 用）：面向「回答质量」的通用问题，用户可在此基础上
# 编辑生成新版本。问题与权重是产品默认值，不是判定算法的一部分。
DEFAULT_RUBRIC_ITEMS: tuple[dict[str, Any], ...] = (
    {
        "key": "answers_question",
        "question": "回答是否直接回应了用户提出的问题（而不是回避或答非所问）？",
        "weight": 3,
        "strictness": "neutral",
    },
    {
        "key": "grounded",
        "question": "回答中的事实性陈述是否与提供的资料一致，没有与资料矛盾的内容？",
        "weight": 3,
        "strictness": "neutral",
    },
    {
        "key": "cites_evidence",
        "question": "回答是否给出了可核对的依据（文档名、条款号或引文）？",
        "weight": 2,
        "strictness": "neutral",
    },
    {
        "key": "no_fabrication",
        "question": "回答是否没有编造数字、日期、条款号或来源？",
        "weight": 3,
        "strictness": "strict",
    },
    {
        "key": "actionable",
        "question": "回答是否给出了明确可执行的结论或下一步？",
        "weight": 1,
        "strictness": "lenient",
    },
)


def default_items() -> list[dict[str, Any]]:
    """默认评分项（深拷贝；调用方不得改共享常量）。"""
    return [dict(item) for item in DEFAULT_RUBRIC_ITEMS]


async def bootstrap_rubric(
    session: AsyncSession,
    *,
    organization_id: int,
    created_by: int,
    name: str = "默认回答质量评分表",
) -> tuple[EvalRubric, EvalRubricVersion]:
    """用服务端默认评分项初始化一个评分表版本（新组织的平滑入口）。"""
    try:
        rubric = await create_rubric(
            session,
            organization_id=organization_id,
            created_by=created_by,
            name=name,
            description="从服务端默认评分项初始化",
            commit=False,
        )
        version = await create_version(
            session,
            rubric=rubric,
            items=default_items(),
            created_by=created_by,
            change_note="bootstrap from server default rubric",
            source="bootstrap",
            commit=False,
        )
        await session.commit()
    except IntegrityError as exc:
        # create_version 的 SELECT 会 autoflush 待提交的表头；重名冲突可能早于
        # 显式 commit 抛出，必须在整个原子写入块外统一映射为 409。
        await session.rollback()
        raise ConflictError("同名评分表已存在", error_code="EVAL_RUBRIC_NAME_CONFLICT") from exc
    await session.refresh(rubric)
    await session.refresh(version)
    return rubric, version


async def list_versions(
    session: AsyncSession,
    *,
    organization_id: int,
    rubric: EvalRubric,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    conditions = [
        EvalRubricVersion.rubric_id == rubric.id,
        EvalRubricVersion.organization_id == organization_id,
    ]
    total = int(
        (
            await session.execute(select(func.count(EvalRubricVersion.id)).where(*conditions))
        ).scalar()
        or 0
    )
    rows = (
        (
            await session.execute(
                select(EvalRubricVersion)
                .where(*conditions)
                .order_by(EvalRubricVersion.version_no.desc(), EvalRubricVersion.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [version_to_dict(row, include_items=False) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "latest_version": int(rubric.latest_version or 0),
    }


async def archive_rubric(session: AsyncSession, *, rubric: EvalRubric, user_id: int) -> EvalRubric:
    """归档评分表（只读终态）。重复归档 409；不删除任何行、不改写历史运行。"""
    if rubric.status == RUBRIC_STATUS_ARCHIVED:
        raise ConflictError(
            "评分表已归档",
            error_code="EVAL_RUBRIC_ALREADY_ARCHIVED",
            detail={"rubric_id": rubric.id},
        )
    rubric.status = RUBRIC_STATUS_ARCHIVED
    rubric.archived_at = _now()
    rubric.archived_by = user_id
    rubric.updated_by = user_id
    rubric.lock_version = int(rubric.lock_version or 1) + 1
    await session.commit()
    await session.refresh(rubric)
    return rubric


# ── 只读 diff ───────────────────────────────────────────────────────────────


def diff_versions(base_row: EvalRubricVersion, target_row: EvalRubricVersion) -> dict[str, Any]:
    """两个版本之间的只读差异（按 item key 对齐，逐字段给出两侧值）。"""
    base_items = base_row.payload_json if isinstance(base_row.payload_json, list) else []
    target_items = target_row.payload_json if isinstance(target_row.payload_json, list) else []
    base_by_key = {entry.get("key"): entry for entry in base_items if isinstance(entry, dict)}
    target_by_key = {entry.get("key"): entry for entry in target_items if isinstance(entry, dict)}

    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    unchanged: list[str] = []
    for key, target in target_by_key.items():
        base = base_by_key.get(key)
        if base is None:
            added.append(target)
            continue
        field_changes: dict[str, Any] = {}
        for field in ("question", "weight", "strictness"):
            base_value = base.get(field)
            target_value = target.get(field)
            if base_value != target_value:
                field_changes[field] = {"base": base_value, "target": target_value}
        if field_changes:
            changed.append({"key": key, "fields": field_changes})
        else:
            unchanged.append(key)
    for key, base in base_by_key.items():
        if key not in target_by_key:
            removed.append(base)

    return {
        "base": {
            "id": base_row.id,
            "version_no": int(base_row.version_no),
            "payload_sha256": base_row.payload_sha256,
            "item_count": int(base_row.item_count),
        },
        "target": {
            "id": target_row.id,
            "version_no": int(target_row.version_no),
            "payload_sha256": target_row.payload_sha256,
            "item_count": int(target_row.item_count),
        },
        "identical": not added and not removed and not changed,
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged_keys": sorted(unchanged),
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "unchanged": len(unchanged),
        },
    }


# ── 判定与评分（纯函数，可无网络单测）──────────────────────────────────────


def verdict_credit(verdict: str, strictness: str) -> float:
    """判定 → 折算分（strictness 只影响 unsure）。"""
    if verdict == "yes":
        return 1.0
    if verdict == "no":
        return 0.0
    if verdict == "unsure":
        return REMAPPED_UNSURE_CREDIT.get(strictness, 0.5)
    raise ValidationError(f"非法判定值: {verdict!r}", field="verdict")


def score_verdicts(items: list[dict[str, Any]], verdicts: dict[str, str]) -> dict[str, Any]:
    """给定评分项与逐项判定，计算一条用例的加权得分明细。"""
    total_weight = sum(int(item["weight"]) for item in items)
    if total_weight <= 0:  # normalize_rubric_items 已保证，防御性保留
        raise ValidationError("评分项权重和为 0", field="items")
    detail: list[dict[str, Any]] = []
    weighted = 0.0
    for item in items:
        verdict = verdicts[item["key"]]
        credit = verdict_credit(verdict, item["strictness"])
        weighted += int(item["weight"]) * credit
        detail.append(
            {
                "key": item["key"],
                "verdict": verdict,
                "credit": credit,
                "weight": int(item["weight"]),
                "strictness": item["strictness"],
            }
        )
    return {
        "score": round(weighted / total_weight, 4),
        "items": detail,
        "weight_total": total_weight,
    }


def validate_verdicts(raw: Any, items: list[dict[str, Any]]) -> dict[str, str]:
    """评委输出必须是 ``{item_key: yes|no|unsure}``，键集合与评分项严格一致。

    缺键/多键/非法值都是**契约违规**（运行显式失败），绝不补默认值蒙混过关。
    """
    if not isinstance(raw, dict):
        raise RubricJudgeContractError("评委输出不是 JSON 对象")
    expected = {item["key"] for item in items}
    actual = set(raw.keys())
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(str(key) for key in actual - expected)
        raise RubricJudgeContractError(
            f"评委输出键集合与评分项不一致（missing={missing}, extra={extra}）"
        )
    verdicts: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or value.strip().lower() not in VERDICTS:
            raise RubricJudgeContractError(f"评委对 {key!r} 返回非法判定: {value!r}")
        verdicts[key] = value.strip().lower()
    return verdicts


def calibrate(
    cases: list[dict[str, Any]],
    items: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """评委判定 vs 人工金标：一致率、逐问题一致率、3×3 混淆矩阵。

    ``cases`` 与 ``results`` 按下标一一对应；只用同时存在 judge 判定与 gold
    判定的 (case, question) 对参与统计。没有任何可比对时返回 ``None``——
    「没有金标」绝不能被展示成「100% 准确」。
    """
    keys = [item["key"] for item in items]
    confusion: dict[str, dict[str, int]] = {
        judge: {gold: 0 for gold in VERDICTS} for judge in VERDICTS
    }
    per_question: dict[str, dict[str, int]] = {key: {"compared": 0, "agreed": 0} for key in keys}
    compared = 0
    agreed = 0
    cases_with_gold = 0
    for case, result in zip(cases, results, strict=True):
        gold = case.get("gold_verdicts")
        if not isinstance(gold, dict) or not gold:
            continue
        cases_with_gold += 1
        judge_by_key = {entry["key"]: entry["verdict"] for entry in result["items"]}
        for key in keys:
            gold_verdict = gold.get(key)
            if gold_verdict not in VERDICTS:
                continue
            judge_verdict = judge_by_key[key]
            compared += 1
            confusion[judge_verdict][gold_verdict] += 1
            per_question[key]["compared"] += 1
            if judge_verdict == gold_verdict:
                agreed += 1
                per_question[key]["agreed"] += 1
    if compared == 0:
        return None
    return {
        "agreement_rate": round(agreed / compared, 4),
        "pairs_compared": compared,
        "pairs_agreed": agreed,
        "cases_with_gold": cases_with_gold,
        "cases_total": len(cases),
        "labels": list(VERDICTS),
        "confusion": confusion,
        "questions": [
            {
                "key": key,
                "compared": per_question[key]["compared"],
                "agreed": per_question[key]["agreed"],
                "agreement_rate": (
                    round(per_question[key]["agreed"] / per_question[key]["compared"], 4)
                    if per_question[key]["compared"]
                    else None
                ),
            }
            for key in keys
        ],
        "note": "一致率仅覆盖带人工金标的用例；不是 judge 的 ground truth 准确率",
    }


def aggregate_results(
    items: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """聚合：宏平均总分 + 逐问题通过率 + 判定分布 + 校准报告。"""
    scores = [result["score"] for result in results]
    question_rows: list[dict[str, Any]] = []
    for item in items:
        key = item["key"]
        pass_count = 0
        credit_sum = 0.0
        counts = {verdict: 0 for verdict in VERDICTS}
        for result in results:
            entry = next(e for e in result["items"] if e["key"] == key)
            counts[entry["verdict"]] += 1
            credit_sum += entry["credit"]
            if entry["credit"] >= 1.0:
                pass_count += 1
        n = len(results)
        question_rows.append(
            {
                "key": key,
                "question": item["question"],
                "weight": int(item["weight"]),
                "strictness": item["strictness"],
                "pass_rate": round(pass_count / n, 4) if n else None,
                "mean_credit": round(credit_sum / n, 4) if n else None,
                "verdict_counts": counts,
            }
        )
    return {
        "score": round(sum(scores) / len(scores), 4) if scores else None,
        "cases_scored": len(results),
        "questions": question_rows,
        "calibration": calibrate(cases, items, results),
        "note": "rubric 得分是模型/确定性判定下的加权通过率，不是人工真值",
    }


# ── 运行输入规范化 ─────────────────────────────────────────────────────────


def normalize_cases(
    raw_cases: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    items: list[dict[str, Any]],
    *,
    mode: str,
) -> list[dict[str, Any]]:
    """校验并规范化运行用例（case_id 唯一、答案长度、金标/回放判定键合法）。

    ``fake`` 模式要求每条用例都提供 ``fake_verdicts`` 且覆盖全部评分项 ——
    fake 是确定性回放，缺判定就是输入错误，绝不补默认值。
    """
    if mode not in RUN_MODES:
        raise ValidationError(f"未知运行模式: {mode!r}", field="mode")
    if not isinstance(raw_cases, (list, tuple)) or not raw_cases:
        raise ValidationError("至少需要一条评测用例", field="cases")
    if len(raw_cases) > MAX_CASES_PER_RUN:
        raise ValidationError(
            f"单次运行最多 {MAX_CASES_PER_RUN} 条用例",
            field="cases",
            expected=MAX_CASES_PER_RUN,
            found=len(raw_cases),
        )
    expected_keys = {item["key"] for item in items}
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw_cases):
        if not isinstance(entry, dict):
            raise ValidationError("评测用例必须是对象", field="cases")
        case_id = str(entry.get("case_id") or "").strip()
        if not case_id:
            raise ValidationError(f"第 {index + 1} 条用例缺少 case_id", field="cases.case_id")
        if len(case_id) > MAX_CASE_ID_CHARS:
            raise ValidationError(
                f"case_id 不能超过 {MAX_CASE_ID_CHARS} 字符", field="cases.case_id"
            )
        if case_id in seen:
            raise ValidationError(f"case_id 重复: {case_id!r}", field="cases.case_id")
        seen.add(case_id)
        answer = entry.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise ValidationError(f"{case_id!r} 的 answer 不能为空", field="cases.answer")
        if len(answer) > MAX_ANSWER_CHARS:
            raise ValidationError(
                f"{case_id!r} 的 answer 不能超过 {MAX_ANSWER_CHARS} 字符",
                field="cases.answer",
            )
        name = entry.get("name")
        if name is not None:
            name = str(name).strip() or None
            if name is not None and len(name) > MAX_CASE_NAME_CHARS:
                raise ValidationError(
                    f"用例名称不能超过 {MAX_CASE_NAME_CHARS} 字符", field="cases.name"
                )
        gold_raw = entry.get("gold_verdicts")
        gold = _normalize_verdict_map(gold_raw, expected_keys, field="cases.gold_verdicts")
        fake_raw = entry.get("fake_verdicts")
        fake = _normalize_verdict_map(fake_raw, expected_keys, field="cases.fake_verdicts")
        if mode == RUN_MODE_FAKE:
            if fake is None:
                raise ValidationError(
                    f"{case_id!r} 在 fake 模式下必须提供 fake_verdicts",
                    field="cases.fake_verdicts",
                )
            missing = sorted(expected_keys - set(fake))
            if missing:
                raise ValidationError(
                    f"{case_id!r} 的 fake_verdicts 缺少评分项: {missing}",
                    field="cases.fake_verdicts",
                )
        normalized.append(
            {
                "case_id": case_id,
                "name": name,
                "answer": answer,
                "gold_verdicts": gold,
                "fake_verdicts": fake,
            }
        )
    return normalized


def _normalize_verdict_map(
    raw: Any, expected_keys: set[str], *, field: str
) -> dict[str, str] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationError(f"{field} 必须是 {{评分项 key: 判定}} 对象", field=field)
    unknown = sorted(str(key) for key in set(raw) - expected_keys)
    if unknown:
        raise ValidationError(f"{field} 含未知评分项 key: {unknown}", field=field)
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or value.strip().lower() not in VERDICTS:
            raise ValidationError(
                f"{field} 中 {key!r} 的判定必须是 {'/'.join(VERDICTS)} 之一", field=field
            )
        normalized[key] = value.strip().lower()
    return normalized or None


def case_set_fingerprint(cases: list[dict[str, Any]]) -> str:
    """用例集稳定哈希：case_id + answer + 金标判定 + 回放判定（排序后序列化）。"""
    payload = sorted(
        (
            case["case_id"],
            case["answer"],
            tuple(sorted((case.get("gold_verdicts") or {}).items())),
            tuple(sorted((case.get("fake_verdicts") or {}).items())),
        )
        for case in cases
    )
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _judge_model_label(mode: str, judge_model: str | None) -> str:
    if mode == RUN_MODE_FAKE:
        return FAKE_JUDGE_MODEL
    model = (judge_model or "").strip()
    return model[:128] if model else "unknown"


# ── 运行执行 ────────────────────────────────────────────────────────────────


async def execute_run(
    session: AsyncSession,
    *,
    organization_id: int,
    rubric: EvalRubric,
    version_no: int | None,
    created_by: int,
    mode: str,
    raw_cases: list[dict[str, Any]],
    verdict_fn: VerdictFn | None = None,
    judge_model: str | None = None,
) -> EvalRubricRun:
    """执行一次评分表评测并落库（成功与失败都留一行快照）。

    ``judge`` 模式要求注入 ``verdict_fn``（端点用真实 LLM 实现，测试用替身）；
    任何评委失败都转成 ``status=failed`` 运行（含机器码、无任何分数），
    由端点映射为 502 —— 不在这里抛异常，保证失败运行一定被持久化。
    """
    if rubric.status != RUBRIC_STATUS_ACTIVE:
        raise ConflictError(
            "评分表已归档：不能发起新的评测运行",
            error_code="EVAL_RUBRIC_ARCHIVED",
            detail={"rubric_id": rubric.id},
        )
    target_no = version_no if version_no is not None else int(rubric.latest_version or 0)
    if target_no <= 0:
        raise ValidationError("该评分表还没有任何版本，不能运行", field="rubric_version_no")
    version = await get_version(
        session, organization_id=organization_id, rubric=rubric, version_no=target_no
    )
    items = version_items(version)
    if items is None:
        # 内容哈希不符 = 数据损坏/被篡改；绝不把不可信评分项当作运行依据。
        raise ConflictError(
            "评分表版本内容校验失败，拒绝使用",
            error_code="EVAL_RUBRIC_VERSION_CORRUPT",
            detail={"rubric_id": rubric.id, "version_no": target_no},
        )
    cases = normalize_cases(raw_cases, items, mode=mode)
    if mode == RUN_MODE_JUDGE and verdict_fn is None:
        raise ValidationError("judge 模式缺少评委实现", field="mode")

    results: list[dict[str, Any]] = []
    error_code: str | None = None
    if mode == RUN_MODE_FAKE:
        for case in cases:
            scored = score_verdicts(items, case["fake_verdicts"] or {})
            results.append(_result_row(case, scored))
    else:
        deadline = time.monotonic() + max(MAX_JUDGE_RUN_SECONDS, 0.0)
        for case in cases:
            # 每条开始前检查：预算为 0（或已耗尽）时必须立刻失败，而不是先把
            # 当前这条跑完再判断——否则「已超时」与「刚好跑完」的边界不确定。
            if time.monotonic() >= deadline:
                error_code = "JUDGE_RUN_BUDGET_EXCEEDED"
                results = []
                break
            try:
                raw_verdicts = await verdict_fn(case, items)  # type: ignore[misc]
                verdicts = validate_verdicts(raw_verdicts, items)
            except RubricJudgeError as exc:
                # 显式失败：任何一条用例判定不可知，整次运行不可知 —— 不给部分
                # 分数，也不把失败用例按 0 分计入（那会把故障伪装成低质量）。
                error_code = exc.error_code
                results = []
                break
            scored = score_verdicts(items, verdicts)
            results.append(_result_row(case, scored))

    run = EvalRubricRun(
        id=str(uuid.uuid4()),
        organization_id=organization_id,
        rubric_id=rubric.id,
        rubric_version_no=target_no,
        dataset_hash=case_set_fingerprint(cases),
        mode=mode,
        status=RUN_STATUS_FAILED if error_code else RUN_STATUS_COMPLETED,
        aggregate=None if error_code else aggregate_results(items, cases, results),
        results=None if error_code else results,
        case_count=len(cases),
        judge_model=_judge_model_label(mode, judge_model),
        error_code=error_code,
        created_by=created_by,
        completed_at=_now(),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


def _result_row(case: dict[str, Any], scored: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "name": case["name"],
        "answer": case["answer"],
        "score": scored["score"],
        "weight_total": scored["weight_total"],
        "items": scored["items"],
        "gold_verdicts": case.get("gold_verdicts"),
    }


# ── 运行查询 ────────────────────────────────────────────────────────────────


async def list_runs(
    session: AsyncSession,
    *,
    organization_id: int,
    rubric: EvalRubric | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    conditions = [EvalRubricRun.organization_id == organization_id]
    if rubric is not None:
        conditions.append(EvalRubricRun.rubric_id == rubric.id)
    total = int(
        (await session.execute(select(func.count(EvalRubricRun.id)).where(*conditions))).scalar()
        or 0
    )
    rows = (
        (
            await session.execute(
                select(EvalRubricRun)
                .where(*conditions)
                .order_by(EvalRubricRun.created_at.desc(), EvalRubricRun.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [run_summary_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_run(session: AsyncSession, *, organization_id: int, run_id: str) -> EvalRubricRun:
    """读取本组织运行；不存在/跨组织一律 404（不回显存在性）。"""
    row = await session.get(EvalRubricRun, run_id)
    if row is None or row.organization_id != organization_id:
        raise NotFoundError("评测运行不存在", resource="eval_rubric_run", resource_id=run_id)
    return row


# ── LLM 评委（端点注入；服务层提供实现以便复用与单测）──────────────────────

JUDGE_SYSTEM_PROMPT = (
    "你是一个严格遵循评分表的评审员。你会收到若干 yes/no 问题与一段候选答案。"
    "对每个问题，只能依据候选答案本身作出判断：\n"
    "- 答案为「是」→ yes；\n"
    "- 答案为「否」→ no；\n"
    "- 候选答案没有提供足够信息、无法判断 → unsure（不要猜测）。\n"
    "只返回一个 JSON 对象，不要输出任何其它文字："
    '{"verdicts": {"<问题 key>": "yes|no|unsure", ...}}。'
    "verdicts 必须恰好包含全部问题 key，不得增删。"
)


def build_judge_prompt(items: list[dict[str, Any]], answer: str) -> str:
    """把评分项与候选答案组装成评委 user 消息（问题正文来自用户编辑，按数据对待）。"""
    lines = ["评分问题（key: 问题）："]
    for item in items:
        lines.append(f"- {item['key']}: {item['question']}")
    lines.append("")
    lines.append("候选答案：")
    lines.append(answer)
    lines.append("")
    lines.append('请只返回 {"verdicts": {...}} 形式的 JSON。')
    return "\n".join(lines)


def _extract_json_object(text: str) -> Any:
    """从评委输出中提取首个 JSON 对象（容忍 ```json 围栏与前后说明文字）。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        # 去掉首行围栏与尾行围栏。
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise RubricJudgeContractError("评委输出不是 JSON 对象") from None
        try:
            return json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            raise RubricJudgeContractError("评委输出不是合法 JSON") from None


def make_llm_verdict_fn(client: Any, model: str) -> VerdictFn:
    """构造真实 LLM 评委函数：严格 JSON 契约 + 明确失败（不静默给 0 分）。

    调用异常 → ``RubricJudgeUnavailableError``；输出非 JSON/缺键/非法值 →
    ``RubricJudgeContractError``。两者都由 ``execute_run`` 转成失败运行。
    """

    async def _verdict_fn(case: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, str]:
        prompt = build_judge_prompt(items, case["answer"])
        messages = [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        try:
            # 走仓库统一的连接级 fast-fail：端点不可达时 1s 内失败，
            # 而不是每道题都等满 60s 读超时（50 题会放大到 50 分钟）。
            response = await create_chat_completion(
                client,
                read_timeout=60.0,
                model=model,
                messages=messages,
                temperature=0.0,
                # 最多 30 个条目键的 JSON；推理模型的 reasoning_content 会占用
                # 预算，800 会把内容挤空（见 core_tools.py 的同型先例）。
                max_tokens=4000,
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                # 推理模型偶发把预算全花在 reasoning_content 上：重试一次再判失败，
                # 避免单次空响应把整次运行打成 contract violation。
                retry = await create_chat_completion(
                    client,
                    read_timeout=60.0,
                    model=model,
                    messages=messages,
                    temperature=0.0,
                    max_tokens=4000,
                )
                content = retry.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001 - 统一转成显式的评委不可用
            raise RubricJudgeUnavailableError(
                f"rubric judge call failed: {type(exc).__name__}"
            ) from exc
        payload = _extract_json_object(content)
        if not isinstance(payload, dict):
            raise RubricJudgeContractError("评委输出不是 JSON 对象")
        if "verdicts" not in payload or not isinstance(payload["verdicts"], dict):
            raise RubricJudgeContractError("评委输出缺少 verdicts 对象")
        return payload["verdicts"]

    return _verdict_fn
