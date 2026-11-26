"""答案质量评测服务：把一次 LLM-as-judge 运行固化为不可变快照。

边界与复用：

- 评分核心完全复用 ``app.rag.evaluator``（``RAGEvaluator`` 的三段提示词与
  连续分解析），本模块不复制任何评分逻辑；``judge`` 模式注入该 evaluator，
  测试可注入确定性替身；
- ``fake`` 模式回放调用方提供的逐项分数，不调用模型；缺分数是输入错误，
  绝不补默认值（否则「没测」会被伪装成「测得 0 分」）；
- 与 ``retrieval_eval``（检索 recall）、``eval_rubric``（加权 yes/no 评分表）
  不共用运行表；本模块是固定维度连续分评委的运行历史。

隐私：落库结果只含指标、有界评审理由、来源哈希与固定占位预览；原始
query/answer/context 文本绝不写入 DB 或 API 响应。评委理由在落库前会做
敏感片段筛查（命中任一 12 字符片段即整条丢弃），避免模型转述原文。

失败：评委不可用/输出不符合契约 → 运行 ``status='failed'`` +
``error_code``（``JUDGE_UNAVAILABLE`` / ``JUDGE_CONTRACT_VIOLATION``），
``aggregate`` / ``results`` 为 NULL；端点映射为 502，绝不返回 0 分。
LLM 未配置是请求前的配置错误（503），不产生运行行——这属于「没有发起
评测」而不是「评测失败」。

幂等：唯一键 ``(organization_id, user_id, dataset_hash, judge_model,
prompt_version, mode, status)``。成功配置重复提交返回既有运行（幂等重放）；
失败后重跑同一配置成功会新增 completed 行（允许恢复），再次失败回读既有
failed 行（不重复堆积）。
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, ValidationError
from app.models.answer_quality_eval import (
    ANSWER_QUALITY_MODES,
    AnswerQualityEvalRun,
)
from app.rag import evaluator as rag_evaluator
from app.rag.context_compressor import get_prompt_text
from app.rag.errors import EvalJudgeContractError, EvalJudgeUnavailableError

# ── 上限（与 schema Field 约束同值；服务层重复校验防内部调用绕过）────────────
MAX_CASES_PER_RUN = 20
MAX_CASE_ID_CHARS = 64
MAX_QUESTION_CHARS = 2000
MAX_ANSWER_CHARS = 10_000
MAX_CONTEXT_ITEMS = 50
MAX_REASON_CHARS = 300
MAX_JUDGE_MODEL_CHARS = 128
MAX_PROMPT_VERSION_CHARS = 64

METRICS = ("faithfulness", "relevancy", "context_precision")
"""固定三维指标；顺序即 API/前端展示顺序。"""

RUN_MODE_JUDGE = "judge"
RUN_MODE_FAKE = "fake"

RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"

ERROR_CODE_JUDGE_UNAVAILABLE = "JUDGE_UNAVAILABLE"
ERROR_CODE_JUDGE_CONTRACT = "JUDGE_CONTRACT_VIOLATION"

FAKE_JUDGE_MODEL = "fake"
DEFAULT_JUDGE_MODEL = "unknown"

PROMPT_VERSION = getattr(rag_evaluator, "PROMPT_VERSION", "rag-eval-v1")
"""提示词版本：来自评分核心模块，评分口径变化时必须同步提升。"""


class AnswerQualityJudgeUnavailableError(RuntimeError):
    """评委不可用：本次运行不可知，调用方必须落 failed 行而不是 0 分。"""


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── 输入规范化 ─────────────────────────────────────────────────────────────


def _normalize_scores(raw: Any, *, case_id: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{case_id!r} 在 fake 模式下必须提供 fake_scores", field="cases.fake_scores"
        )
    unexpected = sorted(set(raw) - set(METRICS))
    missing = sorted(set(METRICS) - set(raw))
    if unexpected or missing:
        raise ValidationError(
            f"{case_id!r} 的 fake_scores 必须恰好包含 {list(METRICS)}"
            f"（missing={missing}, extra={unexpected}）",
            field="cases.fake_scores",
        )
    scores: dict[str, float] = {}
    for metric in METRICS:
        value = raw[metric]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(
                f"{case_id!r} 的 fake_scores.{metric} 必须是 0..1 数值",
                field="cases.fake_scores",
            )
        number = float(value)
        if not 0.0 <= number <= 1.0:
            raise ValidationError(
                f"{case_id!r} 的 fake_scores.{metric} 必须在 0..1 之间",
                field="cases.fake_scores",
            )
        scores[metric] = round(number, 4)
    return scores


def normalize_cases(
    raw_cases: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    mode: str,
) -> list[dict[str, Any]]:
    """校验并规范化用例；``fake`` 模式要求每条携带完整的 ``fake_scores``。"""
    if mode not in ANSWER_QUALITY_MODES:
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
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw_cases:
        if not isinstance(entry, dict):
            raise ValidationError("评测用例必须是对象", field="cases")
        case_id = str(entry.get("case_id") or "").strip()
        if not case_id:
            raise ValidationError("评测用例缺少 case_id", field="cases.case_id")
        if len(case_id) > MAX_CASE_ID_CHARS:
            raise ValidationError(
                f"case_id 不能超过 {MAX_CASE_ID_CHARS} 字符", field="cases.case_id"
            )
        if case_id in seen:
            raise ValidationError(f"case_id 重复: {case_id!r}", field="cases.case_id")
        seen.add(case_id)
        question = entry.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValidationError(f"{case_id!r} 的 question 不能为空", field="cases.question")
        if len(question) > MAX_QUESTION_CHARS:
            raise ValidationError(
                f"{case_id!r} 的 question 不能超过 {MAX_QUESTION_CHARS} 字符",
                field="cases.question",
            )
        answer = entry.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise ValidationError(f"{case_id!r} 的 answer 不能为空", field="cases.answer")
        if len(answer) > MAX_ANSWER_CHARS:
            raise ValidationError(
                f"{case_id!r} 的 answer 不能超过 {MAX_ANSWER_CHARS} 字符",
                field="cases.answer",
            )
        raw_context = entry.get("context")
        if raw_context is None:
            raw_context = []
        if not isinstance(raw_context, list):
            raise ValidationError(f"{case_id!r} 的 context 必须是数组", field="cases.context")
        if len(raw_context) > MAX_CONTEXT_ITEMS:
            raise ValidationError(
                f"{case_id!r} 的 context 最多 {MAX_CONTEXT_ITEMS} 条",
                field="cases.context",
            )
        context: list[dict[str, Any]] = []
        for item in raw_context:
            if not isinstance(item, dict):
                # 评分核心只接受 dict（get_prompt_text 依赖键访问）；
                # 非对象条目直接拒绝，避免半途 500。
                raise ValidationError(
                    f"{case_id!r} 的 context 条目必须是对象", field="cases.context"
                )
            normalized_item: dict[str, str] = {}
            for key, value in item.items():
                if isinstance(value, (dict, list)):
                    raise ValidationError(
                        f"{case_id!r} 的 context 字段必须是字符串或数字: {key}",
                        field="cases.context",
                    )
                # 数值/布尔/None 统一转字符串：评分核心只读文本，且必须在
                # 调用评委之前完成类型归一，避免事后 500。
                normalized_item[str(key)] = (
                    "" if value is None else (value if isinstance(value, str) else str(value))
                )
            context.append(normalized_item)
        fake_scores = None
        if mode == RUN_MODE_FAKE:
            fake_scores = _normalize_scores(entry.get("fake_scores"), case_id=case_id)
        normalized.append(
            {
                "case_id": case_id,
                "question": question,
                "answer": answer,
                "context": context,
                "fake_scores": fake_scores,
            }
        )
    return normalized


def case_set_fingerprint(cases: list[dict[str, Any]]) -> str:
    """用例集稳定哈希：case_id+问题+答案+上下文+回放分数（排序后序列化）。"""
    payload = sorted(
        (
            case["case_id"],
            case["question"],
            case["answer"],
            _canonical_json(case["context"]),
            tuple(sorted((case.get("fake_scores") or {}).items())),
        )
        for case in cases
    )
    return _sha256(_canonical_json(payload))


# ── 来源引用（只存哈希 + 脱敏预览）────────────────────────────────────────


REDACTED_PREVIEW = "[redacted]"
"""来源预览固定占位：不保留任何原文前缀（9 字符秘密曾被泄露 8/9）。"""


def _mask_preview(text: Any) -> str | None:
    """来源预览：只返回固定占位，绝不保留原文前缀。

    之前的「前 8 字符」实现对 9 字符秘密等于泄露 8/9；且 ``***`` 与真实
    文本冲突。哈希（``ref_sha256``）保留可关联性，预览只用于展示。
    """
    if text is None:
        return None
    if isinstance(text, str) and not text.strip():
        return None
    return REDACTED_PREVIEW


def _source_refs(case: dict[str, Any]) -> list[dict[str, Any]]:
    """上下文的来源引用：身份哈希 + 固定占位预览，绝不返回原文。"""
    refs: list[dict[str, Any]] = []
    for item in case["context"]:
        text = get_prompt_text(item)
        if not isinstance(text, str):
            # 非字符串值不崩溃、也不泄露：用规范化 JSON 参与哈希。
            text = _canonical_json(item)
        identity = (
            item.get("doc_id") or item.get("document_id") or item.get("chunk_id") or item.get("id")
        )
        identity_text = str(identity) if identity not in (None, "") else text
        refs.append(
            {
                "ref_sha256": _sha256(identity_text or _canonical_json(item)),
                "masked_preview": _mask_preview(text),
                "char_count": len(text),
            }
        )
    return refs


# ── 评分（复用 app.rag.evaluator 核心）────────────────────────────────────

_REASON_SENSITIVE_WINDOW = 12
"""理由中任一 12 字符片段命中原始输入即整条丢弃（防模型转述原文）。"""


def _contains_sensitive_fragment(text: str, sensitive_texts: list[str]) -> bool:
    joined = "\n".join(item for item in sensitive_texts if item)
    if not joined:
        return False
    if len(text) <= _REASON_SENSITIVE_WINDOW:
        return text in joined
    window = _REASON_SENSITIVE_WINDOW
    return any(text[i : i + window] in joined for i in range(len(text) - window + 1))


def _bounded_reason(raw: Any, sensitive_texts: list[str]) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    text = text[:MAX_REASON_CHARS]
    if _contains_sensitive_fragment(text, sensitive_texts):
        return None
    return text


def _numeric_metric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _result_from_evaluation(case: dict[str, Any], result: Any) -> dict[str, Any]:
    details = getattr(result, "details", None)
    details = details if isinstance(details, dict) else {}
    metrics: dict[str, float] = {}
    for metric in METRICS:
        number = _numeric_metric(getattr(result, metric, None))
        if number is None:
            # 缺指标/NaN/非数字：测量不可知，绝不补 0 分。
            raise EvalJudgeContractError(
                f"evaluator returned invalid {metric} score: {getattr(result, metric, None)!r}"
            )
        metrics[metric] = round(number, 4)
    sensitive = [case["question"], case["answer"]]
    for item in case["context"]:
        text = get_prompt_text(item)
        if isinstance(text, str):
            sensitive.append(text)
    reasons = {
        metric: _bounded_reason(details.get(f"{metric}_reason"), sensitive) for metric in METRICS
    }
    return {
        "case_id": case["case_id"],
        "metrics": metrics,
        "reasons": {key: value for key, value in reasons.items() if value is not None},
        "source_refs": _source_refs(case),
    }


def _result_from_fake(case: dict[str, Any]) -> dict[str, Any]:
    scores = case["fake_scores"] or {}
    return {
        "case_id": case["case_id"],
        "metrics": {metric: round(float(scores[metric]), 4) for metric in METRICS},
        "reasons": {},
        "source_refs": _source_refs(case),
    }


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合：三维均值 + 参与计分条数（空结果不应走到这里）。"""
    count = len(results)
    aggregate: dict[str, Any] = {
        metric: (
            round(sum(result["metrics"][metric] for result in results) / count, 4)
            if count
            else None
        )
        for metric in METRICS
    }
    aggregate["cases_scored"] = count
    aggregate["note"] = (
        "faithfulness/relevancy/context_precision 是 LLM-as-judge 的连续分，"
        "不是人工真值；失败运行不产生分数"
    )
    return aggregate


# ── 运行执行 ──────────────────────────────────────────────────────────────


def _judge_model_label(mode: str, judge_model: str | None) -> str:
    if mode == RUN_MODE_FAKE:
        return FAKE_JUDGE_MODEL
    model = (judge_model or "").strip()
    return model[:MAX_JUDGE_MODEL_CHARS] if model else DEFAULT_JUDGE_MODEL


async def _find_existing_run(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    dataset_hash: str,
    judge_model: str,
    prompt_version: str,
    mode: str,
    status: str,
) -> AnswerQualityEvalRun | None:
    return (
        await session.execute(
            select(AnswerQualityEvalRun).where(
                AnswerQualityEvalRun.organization_id == organization_id,
                AnswerQualityEvalRun.user_id == user_id,
                AnswerQualityEvalRun.dataset_hash == dataset_hash,
                AnswerQualityEvalRun.judge_model == judge_model,
                AnswerQualityEvalRun.prompt_version == prompt_version,
                AnswerQualityEvalRun.mode == mode,
                AnswerQualityEvalRun.status == status,
            )
        )
    ).scalar_one_or_none()


async def execute_run(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    created_by: int,
    raw_cases: list[dict[str, Any]],
    mode: str,
    evaluator: Any | None = None,
    judge_model: str | None = None,
    prompt_version: str = PROMPT_VERSION,
) -> AnswerQualityEvalRun:
    """执行一次答案质量评测并落库（成功与失败都留一行快照）。

    ``judge`` 模式要求注入 ``app.rag.evaluator.RAGEvaluator`` 实例；任何评委
    不可用都转成 ``status=failed`` 运行（含机器码、无任何分数），由端点映射
    为 502 —— 不在这里抛异常，保证失败运行一定被持久化。
    """
    cases = normalize_cases(raw_cases, mode=mode)
    if mode == RUN_MODE_JUDGE and evaluator is None:
        raise ValidationError("judge 模式缺少评委实现", field="mode")
    dataset_hash = case_set_fingerprint(cases)
    model_label = _judge_model_label(mode, judge_model)
    version_label = (prompt_version or PROMPT_VERSION)[:MAX_PROMPT_VERSION_CHARS]

    # 幂等：同一配置已成功过 → 直接回放既有快照（不重复烧模型）。
    existing = await _find_existing_run(
        session,
        organization_id=organization_id,
        user_id=user_id,
        dataset_hash=dataset_hash,
        judge_model=model_label,
        prompt_version=version_label,
        mode=mode,
        status=RUN_STATUS_COMPLETED,
    )
    if existing is not None:
        return existing

    results: list[dict[str, Any]] = []
    error_code: str | None = None
    if mode == RUN_MODE_FAKE:
        results = [_result_from_fake(case) for case in cases]
    else:
        for case in cases:
            try:
                evaluation = await evaluator.evaluate(  # type: ignore[union-attr]
                    question=case["question"],
                    answer=case["answer"],
                    context=case["context"],
                )
                results.append(_result_from_evaluation(case, evaluation))
            except EvalJudgeContractError:
                # 输出不可解析/缺指标 = 测量失败，不是 0 分。
                error_code = ERROR_CODE_JUDGE_CONTRACT
                results = []
                break
            except EvalJudgeUnavailableError:
                # 显式失败：任意一条评委不可用 → 整次运行不可知。不给部分分数，
                # 也不把失败条目按 0 分计入（那会把故障伪装成低质量）。
                error_code = ERROR_CODE_JUDGE_UNAVAILABLE
                results = []
                break

    run = AnswerQualityEvalRun(
        id=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=user_id,
        dataset_hash=dataset_hash,
        judge_model=model_label,
        prompt_version=version_label,
        mode=mode,
        status=RUN_STATUS_FAILED if error_code else RUN_STATUS_COMPLETED,
        aggregate=None if error_code else aggregate_results(results),
        results=None if error_code else results,
        case_count=len(cases),
        error_code=error_code,
        created_by=created_by,
        completed_at=_now(),
    )
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:
        # 并发同配置：另一请求已先落库。回读既有行（幂等），绝不静默覆盖。
        await session.rollback()
        duplicate = await _find_existing_run(
            session,
            organization_id=organization_id,
            user_id=user_id,
            dataset_hash=dataset_hash,
            judge_model=model_label,
            prompt_version=version_label,
            mode=mode,
            status=run.status,
        )
        if duplicate is not None:
            return duplicate
        raise
    await session.refresh(run)
    return run


# ── 序列化 ────────────────────────────────────────────────────────────────


def run_summary_dict(row: AnswerQualityEvalRun) -> dict[str, Any]:
    aggregate = row.aggregate if isinstance(row.aggregate, dict) else None
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "user_id": row.user_id,
        "dataset_hash": row.dataset_hash,
        "judge_model": row.judge_model,
        "prompt_version": row.prompt_version,
        "mode": row.mode,
        "status": row.status,
        "case_count": int(row.case_count),
        "metrics": {
            metric: (aggregate.get(metric) if aggregate is not None else None) for metric in METRICS
        },
        "error_code": row.error_code,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def run_detail_dict(row: AnswerQualityEvalRun) -> dict[str, Any]:
    data = run_summary_dict(row)
    data["aggregate"] = row.aggregate if isinstance(row.aggregate, dict) else None
    data["results"] = row.results if isinstance(row.results, list) else None
    return data


# ── 查询 / 趋势 ───────────────────────────────────────────────────────────


def _date_bounds(
    date_from: date | None, date_to: date | None
) -> tuple[datetime | None, datetime | None]:
    """日期过滤边界（含首尾日，UTC）；非法区间显式 422 而不是静默空列表。"""
    start = datetime.combine(date_from, time.min, tzinfo=UTC) if date_from is not None else None
    end = (
        datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=UTC)
        if date_to is not None
        else None
    )
    if start is not None and end is not None and start >= end:
        raise ValidationError("date_from 不能晚于 date_to", field="date_from")
    return start, end


def _filter_conditions(
    *,
    organization_id: int,
    user_id: int | None,
    status: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[Any]:
    if status not in (None, "all", RUN_STATUS_COMPLETED, RUN_STATUS_FAILED):
        raise ValidationError(f"未知运行状态: {status!r}", field="status")
    conditions: list[Any] = [AnswerQualityEvalRun.organization_id == organization_id]
    if user_id is not None:
        conditions.append(AnswerQualityEvalRun.user_id == user_id)
    if status in (RUN_STATUS_COMPLETED, RUN_STATUS_FAILED):
        conditions.append(AnswerQualityEvalRun.status == status)
    start, end = _date_bounds(date_from, date_to)
    if start is not None:
        conditions.append(AnswerQualityEvalRun.created_at >= start)
    if end is not None:
        conditions.append(AnswerQualityEvalRun.created_at < end)
    return conditions


async def list_runs(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int | None = None,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    conditions = _filter_conditions(
        organization_id=organization_id,
        user_id=user_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    total = int(
        (
            await session.execute(select(func.count(AnswerQualityEvalRun.id)).where(*conditions))
        ).scalar()
        or 0
    )
    rows = (
        (
            await session.execute(
                select(AnswerQualityEvalRun)
                .where(*conditions)
                .order_by(AnswerQualityEvalRun.created_at.desc(), AnswerQualityEvalRun.id.desc())
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


async def get_run(
    session: AsyncSession, *, organization_id: int, run_id: str
) -> AnswerQualityEvalRun:
    """读取本组织运行；不存在/跨组织一律 404（不回显存在性）。"""
    row = await session.get(AnswerQualityEvalRun, run_id)
    if row is None or row.organization_id != organization_id:
        raise NotFoundError(
            "评测运行不存在", resource="answer_quality_eval_run", resource_id=run_id
        )
    return row


async def trend(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int | None = None,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """按时间正序返回运行点与汇总；失败运行计入次数但不污染均值。"""
    conditions = _filter_conditions(
        organization_id=organization_id,
        user_id=user_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    # 先取最新 N 条再反转成正序（与 retrieval_eval 相同契约）；用 asc+limit
    # 会静默丢掉最新运行，让趋势停留在最老的窗口。
    rows = list(
        (
            await session.execute(
                select(AnswerQualityEvalRun)
                .where(*conditions)
                .order_by(AnswerQualityEvalRun.created_at.desc(), AnswerQualityEvalRun.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    rows.reverse()
    points: list[dict[str, Any]] = []
    metric_sums = {metric: 0.0 for metric in METRICS}
    scored_runs = 0
    failed_runs = 0
    for row in rows:
        aggregate = row.aggregate if isinstance(row.aggregate, dict) else None
        metrics = {
            metric: (aggregate.get(metric) if aggregate is not None else None) for metric in METRICS
        }
        if row.status == RUN_STATUS_COMPLETED and aggregate is not None:
            scored_runs += 1
            for metric in METRICS:
                value = aggregate.get(metric)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    metric_sums[metric] += float(value)
        else:
            failed_runs += 1
        points.append(
            {
                "id": row.id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "status": row.status,
                "mode": row.mode,
                "judge_model": row.judge_model,
                "prompt_version": row.prompt_version,
                "case_count": int(row.case_count),
                "metrics": metrics,
                "error_code": row.error_code,
            }
        )
    averages = {
        metric: (round(metric_sums[metric] / scored_runs, 4) if scored_runs else None)
        for metric in METRICS
    }
    return {
        "points": points,
        "summary": {
            "runs": len(points),
            "succeeded_runs": scored_runs,
            "failed_runs": failed_runs,
            "averages": averages,
        },
    }


# ── 既有 /monitoring/rag-eval* 的共享评分入口（不复制评分逻辑）────────────


async def evaluate_single(
    evaluator: Any, *, question: str, answer: str, context: list[dict[str, Any]]
) -> dict[str, Any]:
    """单条评分：委托 ``RAGEvaluator.evaluate``，返回既有端点契约的字段。"""
    result = await evaluator.evaluate(question=question, answer=answer, context=context)
    return {
        "faithfulness": result.faithfulness,
        "relevancy": result.relevancy,
        "context_precision": result.context_precision,
        "details": result.details,
    }


async def evaluate_batch(evaluator: Any, items: list[dict[str, Any]]) -> Any:
    """批量评分：委托 ``RAGEvaluator.evaluate_batch``，返回其 BatchEvalResult。"""
    return await evaluator.evaluate_batch(items)
