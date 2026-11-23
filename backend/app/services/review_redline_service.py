"""入向红线台账：对方 DOCX 的 OOXML 修订/批注确定性解析器与数据库服务。

事实边界：本模块只记录「原文里真实存在的文本变化/批注」以及人工处置状态，
不产出风险等级、结论、建议或法律意见。解析完全在内存中完成（zipfile +
defusedxml），不落地临时文件、不调用 LLM/网络/ES。

文本溯源恒为 ``REVIEW_REDLINE_TEXT_PROVENANCE``（counterparty_docx_ooxml），
绝不冒充 ReviewEvidence 的 ``parser_original``。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import uuid
import zipfile
import zlib
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.minio_client import minio_client
from app.core.security import get_user_org_id
from app.exceptions import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from app.models.review import (
    REVIEW_REDLINE_DISPOSITIONS,
    REVIEW_REDLINE_ROW_TYPES,
    REVIEW_REDLINE_TEXT_PROVENANCE,
    ReviewCase,
    ReviewEvidence,
    ReviewItem,
    ReviewRedlineRow,
    ReviewRedlineSubmission,
)
from app.models.user import User
from app.models.user_audit import UserActivityLog
from app.schemas.review import RedlineDispositionUpdate

# ── 公开常量 ──────────────────────────────────────────────────────────
MAX_REDLINE_UPLOAD_BYTES = 20 * 1024 * 1024
"""上传的 DOCX 字节上限；同时作为 ZIP 解压后所有成员的总字节上限。"""

MAX_REDLINE_ROWS = 2000
"""单次上传最多入库的台账行数；超出时保留前 N 行并显式告警为 partial。"""

MAX_REDLINE_TEXT_CHARS = 4000
"""单条 before/after 文本字符上限；超出时截断并显式告警（不静默丢字）。"""

MAX_OOXML_XML_DEPTH = 256
"""祖先链深度硬上限：超深节点留 unparsed 行，绝不让递归耗尽栈。"""

REDLINE_PARSER_VERSION = "redline-ooxml-v1"
parser_version = REDLINE_PARSER_VERSION  # 兼容按小写常量名引用的调用方

REDLINE_DISCLAIMER = "只记录文本变化事实，不构成法律意见"

_DOCUMENT_PART = "word/document.xml"
_COMMENTS_PART = "word/comments.xml"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_FORBIDDEN_KEYS = frozenset({"risk", "verdict", "recommendation", "legal_opinion"})

# ZIP 炸弹守卫：单成员解压后不得超过总预算；高压缩比且体积不小视为可疑。
_ZIP_RATIO_LIMIT = 200
_ZIP_RATIO_MIN_BYTES = 256 * 1024

# ── 可解析的修订元素 ──────────────────────────────────────────────────
_SUPPORTED = {
    "ins": "insertion",
    "del": "deletion",
}

# 显式留档的无法归类结构 → 稳定原因码。绝不静默丢弃这些结构里的文本。
# Word 的范围标记（moveFrom/moveTo/ins/del 的 RangeStart/RangeEnd）本身只描述
# 修订边界，却常是 DOCX 中唯一的修订痕迹；必须逐条留档，不能因「无可见文本」
# 被折叠掉，否则整篇只有范围标记的 DOCX 会伪装成 parsed/0 行/0 告警。
_UNPARSED_REASONS: dict[str, str] = {
    "moveFrom": "move",
    "moveTo": "move",
    "moveFromRangeStart": "move_range",
    "moveFromRangeEnd": "move_range",
    "moveToRangeStart": "move_range",
    "moveToRangeEnd": "move_range",
    "insRangeStart": "revision_range",
    "insRangeEnd": "revision_range",
    "delRangeStart": "revision_range",
    "delRangeEnd": "revision_range",
    "rPrChange": "rPrChange",
    "pPrChange": "pPrChange",
    "sectPrChange": "sectPrChange",
    "tblPrChange": "tblPrChange",
    "trPrChange": "trPrChange",
    "tcPrChange": "tcPrChange",
    "numberingChange": "numberingChange",
    "cellIns": "cellIns",
    "cellDel": "cellDel",
    "cellMerge": "merge",
    "vMerge": "merge",
    "gridMerge": "merge",
    "customXmlInsRangeStart": "customXml",
    "customXmlInsRangeEnd": "customXml",
    "customXmlDelRangeStart": "customXml",
    "customXmlDelRangeEnd": "customXml",
    "customXmlMoveFromRangeStart": "customXml",
    "customXmlMoveFromRangeEnd": "customXml",
    "customXmlMoveToRangeStart": "customXml",
    "customXmlMoveToRangeEnd": "customXml",
}

_CLAUSE_RE = re.compile(r"第\s*([0-9零〇一二三四五六七八九十百千]+)\s*条")
_SECTION_RE = re.compile(r"第\s*([0-9零〇一二三四五六七八九十百千]+)\s*章")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# 服务层只允许这些解析器字段进入 ORM 行，避免解析结果意外覆盖处置/溯源列。
_ROW_FIELDS = (
    "ordinal",
    "revision_type",
    "pair_key",
    "before_text",
    "after_text",
    "before_sha256",
    "after_sha256",
    "author",
    "authored_at",
    "comment_id",
    "comment_author",
    "comment_date",
    "location",
    "clause_no",
    "section_no",
    "unparsed_reason",
    "text_provenance",
)


@dataclass
class RedlineParseResult:
    """解析器公开结果；rows 为可直接用于 ReviewRedlineRow 插入的原生类型字典。"""

    submission_status: str
    revision_count: int
    comment_count: int
    unparsed_count: int
    parse_warnings: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)


def _w(local: str) -> str:
    return f"{{{_W_NS}}}{local}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _is(tag: str, local: str) -> bool:
    return tag == _w(local)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _xml_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


# 批注正文嵌套修订的稳定原因码（schema 允许任意字符串，这里集中定义，禁止临时拼装）。
_COMMENT_REASONS: dict[str, str] = {
    "insertion": "comment_insertion",
    "deletion": "comment_deletion",
    "move": "comment_move",
    "move_range": "comment_move_range",
    "revision_range": "comment_revision_range",
    "rPrChange": "comment_rPrChange",
    "pPrChange": "comment_pPrChange",
    "sectPrChange": "comment_sectPrChange",
    "tblPrChange": "comment_tblPrChange",
    "trPrChange": "comment_trPrChange",
    "tcPrChange": "comment_tcPrChange",
    "numberingChange": "comment_numberingChange",
    "cellIns": "comment_cellIns",
    "cellDel": "comment_cellDel",
    "merge": "comment_merge",
    "customXml": "comment_customXml",
    "nested_revision": "comment_nested_revision",
}

# 侧别感知的文本抽取：w:ins 的 after 不能混入嵌套 w:del 的 delText，
# w:del 的 before 也不能混入嵌套 w:ins 的 w:t；否则「新增里删除」与
# 「删除里新增」会被记成相反方向的单侧改动。
_OPPOSITE_SIDE = {"insertion": "del", "deletion": "ins"}
_TEXT_TAGS = frozenset({"t", "delText", "tab", "br", "cr"})


def _node_text(
    element: Any, *, side: str | None = None, skip_ids: AbstractSet[int] = frozenset()
) -> str:
    """按文档顺序拼接 OOXML 可见文本（含制表/换行与删除文本，不改写字面内容）。

    ``side='insertion'`` 跳过嵌套 w:del 子树，``side='deletion'`` 跳过嵌套
    w:ins 子树；本节点自身永不跳过。``skip_ids`` 供调用方排除已单独留档的
    嵌套结构，防止同一段文本在父子两行里重复计数。
    """
    parts: list[str] = []
    opposite = _OPPOSITE_SIDE.get(side) if side else None
    stack: list[tuple[Any, bool]] = [(element, True)]
    while stack:
        node, included = stack.pop()
        if not included or id(node) in skip_ids:
            continue
        tag = _local_name(node.tag)
        if tag in _TEXT_TAGS:
            if _is(node.tag, "t") or _is(node.tag, "delText"):
                parts.append(node.text or "")
            elif _is(node.tag, "tab"):
                parts.append("\t")
            else:
                parts.append("\n")
        elif tag in {"ins", "del", "comment"} and node.text:
            # 合成 DOCX 与部分生成器会把可见文本直接放在修订/批注容器里；
            # 这段文本与 w:t/w:delText 同样是原文事实，不能丢。
            parts.append(node.text)
        # 子节点逆序入栈以保持文档顺序；字段结果等例外由调用方用 skip_ids 排除。
        child_included = included and not (opposite is not None and _is(node.tag, opposite))
        for child in reversed(list(node)):
            stack.append((child, child_included))
    return "".join(parts)


def _nested_elements(element: Any, local: str) -> list[Any]:
    """返回元素自身之外的所有后代 w:<local>，按文档顺序。"""
    found: list[Any] = []
    for node in element.iter():
        if node is not element and _is(node.tag, local):
            found.append(node)
    return found


def _descendant_revisions(element: Any) -> set[int]:
    """父修订行必须跳过的后代 w:ins/w:del id（同侧与异侧都包括）。

    每个嵌套修订自己单独生成一行；父行不能把它包含的文字再计一遍。
    """
    return {
        id(node)
        for node in element.iter()
        if node is not element and _local_name(node.tag) in _SUPPORTED
    }


def _field_elements(root: Any) -> tuple[set[int], list[tuple[Any, str]]]:
    """收集 Word 字段结构及其结果文本；字段结果不是可判定的修订事实。

    同时支持 ``w:fldSimple`` 与标准 complex field：
    ``fldChar(begin) → instrText → fldChar(separate) → w:r/w:t → fldChar(end)``。
    返回 ``(skip_ids, [(anchor, result_text)])``；skip_ids 让所有事实行共用，
    保证字段结果只出现在显式 unparsed 行里，绝不混入插入/删除事实。
    """
    skip_ids: set[int] = set()
    results: list[tuple[Any, str]] = []

    for node in root.iter():
        if not _is(node.tag, "fldSimple"):
            continue
        anchor: Any | None = None
        parts: list[str] = []
        for child in node.iter():
            skip_ids.add(id(child))
            if child is not node and anchor is None:
                anchor = child
            if _is(child.tag, "t") or _is(child.tag, "delText"):
                parts.append(child.text or "")
        if anchor is not None and parts:
            results.append((anchor, "".join(parts)))

    state = 0
    anchor = None
    parts = []
    for node in root.iter():
        if _is(node.tag, "fldChar"):
            skip_ids.add(id(node))
            kind = (node.get(_w("fldCharType")) or "").strip()
            if kind == "begin":
                state, anchor, parts = 1, node, []
            elif kind == "separate" and state == 1:
                state = 2
            elif kind == "end":
                if state == 2 and parts:
                    results.append((anchor or node, "".join(parts)))
                state, anchor, parts = 0, None, []
            continue
        if _is(node.tag, "instrText"):
            skip_ids.add(id(node))
            continue
        if state != 2:
            continue
        local = _local_name(node.tag)
        if local in {"t", "delText"}:
            skip_ids.add(id(node))
            parts.append(node.text or "")
        elif local == "tab":
            skip_ids.add(id(node))
            parts.append("\t")
        elif local in {"br", "cr"}:
            skip_ids.add(id(node))
            parts.append("\n")
    return skip_ids, results


def _revision_side(local: str) -> str | None:
    return _SUPPORTED.get(local)


def _bounded(value: Any, limit: int) -> Any:
    """把解析出的字符串裁到 ORM 列宽，避免 MySQL 严格模式下整批入库失败。"""
    return value[:limit] if isinstance(value, str) and len(value) > limit else value


def _clause_hints(paragraphs: list[str]) -> tuple[list[str] | None, list[str] | None]:
    clauses: list[str] = []
    sections: list[str] = []
    for text in paragraphs:
        for value in _CLAUSE_RE.findall(text):
            if value not in clauses:
                clauses.append(value)
        for value in _SECTION_RE.findall(text):
            if value not in sections:
                sections.append(value)
    return (clauses or None), (sections or None)


def _parse_xml(payload: bytes, part: str) -> Any:
    try:
        return fromstring(
            payload,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except (DefusedXmlException, ParseError, ValueError) as exc:
        raise ValidationError(
            f"DOCX XML 无法安全解析: {part}",
            field="file",
            detail={"part": part, "reason": str(exc)},
        ) from exc


def _read_zip_parts(data: bytes) -> dict[str, bytes]:
    """从内存字节读取受信任的 OOXML 成员；拒绝加密、缺失与压缩包炸弹。"""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValidationError("DOCX 内容必须是字节", field="file")
    raw = bytes(data)
    if not raw:
        raise ValidationError("DOCX 内容为空", field="file")
    if len(raw) > MAX_REDLINE_UPLOAD_BYTES:
        raise ValidationError(
            "DOCX 超过上传上限",
            field="file",
            detail={"max_bytes": MAX_REDLINE_UPLOAD_BYTES, "received_bytes": len(raw)},
        )
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            total = 0
            for info in infos:
                if info.flag_bits & 0x1:
                    raise ValidationError("DOCX 成员已加密，无法解析", field="file")
                name = info.filename
                if name in {"EncryptionInfo", "EncryptedPackage", "EncryptedPackage2"}:
                    raise ValidationError("加密 DOCX 无法解析", field="file")
                if info.file_size > MAX_REDLINE_UPLOAD_BYTES:
                    raise ValidationError("DOCX 解压后成员过大", field="file")
                if (
                    info.file_size > _ZIP_RATIO_MIN_BYTES
                    and info.compress_size > 0
                    and info.file_size / info.compress_size > _ZIP_RATIO_LIMIT
                ):
                    raise ValidationError("DOCX 压缩比异常（疑似压缩包炸弹）", field="file")
                total += info.file_size
                if total > MAX_REDLINE_UPLOAD_BYTES:
                    raise ValidationError("DOCX 解压后总量超过上限", field="file")
            names = {info.filename for info in infos}
            if _DOCUMENT_PART not in names:
                raise ValidationError("DOCX 缺少 word/document.xml", field="file")
            parts: dict[str, bytes] = {}
            for part in (_DOCUMENT_PART, _COMMENTS_PART):
                if part not in names:
                    continue
                # 声明的 file_size 只是目录项，实际解压流必须同样受限：
                # 逐块读取并在累计超过预算时立即拒绝，防止伪造头+解压炸弹。
                chunks: list[bytes] = []
                decompressed = 0
                with archive.open(part) as stream:
                    while chunk := stream.read(64 * 1024):
                        decompressed += len(chunk)
                        if decompressed > MAX_REDLINE_UPLOAD_BYTES:
                            raise ValidationError("DOCX 成员解压后超过上限", field="file")
                        chunks.append(chunk)
                payload = b"".join(chunks)
                parts[part] = payload
            return parts
    except zipfile.BadZipFile as exc:
        raise ValidationError("文件不是有效的 DOCX（ZIP）格式", field="file") from exc
    except ValidationError:
        raise
    except (
        zipfile.LargeZipFile,
        NotImplementedError,
        zlib.error,
        RuntimeError,
        EOFError,
        OSError,
    ) as exc:
        # 不支持的压缩方法(99)、损坏的 deflate 流、被截断的成员都必须 400，
        # 不能让内部异常穿透成 500。
        raise ValidationError("DOCX 成员无法安全解压", field="file") from exc


def _comments_by_id(root: Any | None) -> dict[str, dict[str, Any]]:
    if root is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for element in root.iter():
        if not _is(element.tag, "comment"):
            continue
        comment_id = (element.get(_w("id")) or "").strip()
        if not comment_id or comment_id in result:
            continue
        # 批注正文里的嵌套修订由独立的 unparsed 行留档；从批注文本中剔除，
        # 防止同一段文字在 comment 行与 comment_revision 行重复计数。
        nested_ids = frozenset(
            id(node)
            for node in element.iter()
            if node is not element
            and (
                _local_name(node.tag) in _SUPPORTED
                or _local_name(node.tag) in _UNPARSED_REASONS
                or _is(node.tag, "nestedRevision")
            )
        )
        result[comment_id] = {
            "text": _node_text(element, skip_ids=nested_ids),
            "author": (element.get(_w("author")) or "").strip() or None,
            "date": _xml_date(element.get(_w("date"))),
            # 正文只由嵌套修订组成时 text 为空，但批注本身非空；用于避免
            # 把「只有修订的批注」误报为空批注。
            "has_nested": bool(nested_ids),
        }
    return result


def _ancestors(root: Any) -> tuple[dict[int, tuple[str, ...]], set[int]]:
    """元素 id → 父级 tag 链；显式栈迭代，超深节点只保留末 N 层祖先。

    返回 ``(chains, deep_ids)``：deep_ids 中的节点必须落 unparsed 行（原因码
    xml_depth_cap），避免深层文档触发 RecursionError 或被静默跳过。
    """
    chains: dict[int, tuple[Any, ...]] = {}
    deep_ids: set[int] = set()
    stack: list[tuple[Any, tuple[Any, ...], int]] = [(root, (), 1)]
    while stack:
        element, parents, depth = stack.pop()
        chains[id(element)] = parents
        if depth > MAX_OOXML_XML_DEPTH:
            deep_ids.add(id(element))
        child_parents = (*parents, element)[-MAX_OOXML_XML_DEPTH:]
        for child in reversed(list(element)):
            stack.append((child, child_parents, depth + 1))
    return chains, deep_ids


def _is_revision_container(local: str) -> bool:
    """可能包裹修订的结构容器：重叠必须显式告警，不静默折叠。"""
    return local in {
        "customXml",
        "nestedRevision",
        "sdt",
        "sdtContent",
        "sdtContentBlock",
    } or local.startswith("customXml")


def _paragraph_hints(element: Any, parents: list[Any]) -> tuple[list[str] | None, list[str] | None]:
    texts: list[str] = []
    for node in parents:
        if _is(node.tag, "p"):
            texts.append(_node_text(node))
            break
    return _clause_hints(texts)


def parse_redline_docx(data: bytes, filename: str = "") -> RedlineParseResult:
    """确定性解析对方 DOCX 的修订/批注；不产生任何判定性字段。

    ``filename`` 只用于未来的诊断扩展，解析结果不依赖它（同字节同结果）。
    """
    del filename  # 明确：解析结果只由字节决定
    parts = _read_zip_parts(data)
    document = _parse_xml(parts[_DOCUMENT_PART], _DOCUMENT_PART)
    comments_root = (
        _parse_xml(parts[_COMMENTS_PART], _COMMENTS_PART) if _COMMENTS_PART in parts else None
    )
    comments = _comments_by_id(comments_root)
    warnings: list[str] = []
    events: list[dict[str, Any]] = []
    ancestors, deep_ids = _ancestors(document)
    if deep_ids:
        warnings.append(f"xml_depth_cap_exceeded:nodes={len(deep_ids)};max={MAX_OOXML_XML_DEPTH}")

    seen_comments: set[str] = set()
    document_order = {id(node): index for index, node in enumerate(document.iter())}
    field_skip_ids, field_results = _field_elements(document)
    # 嵌套修订由父行单独留档；这里记录其 id，避免子修订再生成一行造成双计。
    attributed_nested_ids: set[int] = set()
    order = 0
    for element in document.iter():
        tag = element.tag
        local = _local_name(tag)
        parents = ancestors.get(id(element), ())
        parent_locals = [_local_name(parent.tag) for parent in parents]
        order = document_order[id(element)]

        if id(element) in attributed_nested_ids:
            # 嵌套修订由父修订行统一留档，文本只在那里出现一次。
            continue

        if id(element) in deep_ids:
            warnings.append(f"xml_depth_cap_exceeded:element=w:{local}")
            events.append(
                {
                    "kind": "unparsed",
                    "text_before": _node_text(element) or None,
                    "text_after": None,
                    "unparsed_reason": ["xml_depth_cap"],
                    "element": local,
                    "order": order,
                    "location": {
                        "source": _DOCUMENT_PART,
                        "element": f"w:{local}",
                        "order": order,
                        "depth_cap": MAX_OOXML_XML_DEPTH,
                    },
                    "clause_no": None,
                    "section_no": None,
                }
            )
            continue

        if local in _SUPPORTED:
            revision_type = _SUPPORTED[local]
            # 父修订行必须跳过所有后代修订（同侧与异侧都由各自逻辑归因），
            # 否则同一段文字会在父行与子行里各计一次。
            descendants = _descendant_revisions(element)
            attributed_nested_ids.update(descendants)
            sibling_descendants = descendants - {id(element)}
            # 同侧嵌套的文字并入父事实行（只算一次）；异侧嵌套由下面的
            # unparsed 行单独承载，必须从父行剔除，否则方向被记反。
            opposite = _OPPOSITE_SIDE[revision_type]
            skip_ids = set(field_skip_ids)
            skip_ids.update(
                id(node)
                for node in element.iter()
                if id(node) in sibling_descendants and _local_name(node.tag) == opposite
            )
            text = _node_text(element, side=revision_type, skip_ids=skip_ids)
            if revision_type == "insertion":
                before, after = None, text or None
            else:
                before, after = text or None, None
            clause_no, section_no = _paragraph_hints(element, parents)
            nested = [
                parent_local
                for parent_local in parent_locals
                if parent_local in _SUPPORTED
                or parent_local in _UNPARSED_REASONS
                or _is_revision_container(parent_local)
            ]
            if nested:
                warnings.append(f"overlap:{nested[-1]}:{local}")
            # 异侧嵌套修订单独留档；同侧嵌套的文字已合并进父事实行，
            # 只记 overlap 告警，不再生成第二行。
            for nested_node in element.iter():
                if id(nested_node) not in sibling_descendants:
                    continue
                nested_local = _local_name(nested_node.tag)
                nested_type = _SUPPORTED.get(nested_local)
                if nested_type is None or nested_type == revision_type:
                    continue
                nested_skip = set()
                nested_skip.update(
                    id(child)
                    for child in nested_node.iter()
                    if child is not nested_node and _local_name(child.tag) in _SUPPORTED
                )
                # 字段结果只在 dedicated field_result_revision 行里承载；
                # 这里按局部字段结构跳过，避免把字段文本混进嵌套修订方向行。
                nested_field_skip, _ = _field_elements(nested_node)
                nested_skip.update(nested_field_skip)
                nested_text = _node_text(nested_node, side=nested_type, skip_ids=nested_skip)
                warnings.append(f"nested_opposite_revision:outer={local};inner={nested_local}")
                nested_clause, nested_section = _paragraph_hints(nested_node, parents)
                events.append(
                    {
                        "kind": "unparsed",
                        "text_before": nested_text or None,
                        "text_after": None,
                        "author": (nested_node.get(_w("author")) or "").strip() or None,
                        "authored_at": _xml_date(nested_node.get(_w("date"))),
                        "unparsed_reason": ["nested_opposite_revision"],
                        "element": nested_local,
                        "order": document_order[id(nested_node)],
                        "location": {
                            "source": _DOCUMENT_PART,
                            "element": f"w:{nested_local}",
                            "outer": f"w:{local}",
                        },
                        "clause_no": nested_clause,
                        "section_no": nested_section,
                    }
                )
            events.append(
                {
                    "kind": revision_type,
                    "text_before": before,
                    "text_after": after,
                    "author": (element.get(_w("author")) or "").strip() or None,
                    "authored_at": _xml_date(element.get(_w("date"))),
                    "element": local,
                    "order": order,
                    "location": {
                        "source": _DOCUMENT_PART,
                        "element": f"w:{local}",
                        "order": order,
                    },
                    "clause_no": clause_no,
                    "section_no": section_no,
                }
            )
            continue

        if local in {"commentRangeStart", "commentReference"}:
            comment_id = (element.get(_w("id")) or "").strip()
            if not comment_id:
                warnings.append(f"missing_comment_id:w:{local}")
                continue
            if comment_id in seen_comments:
                continue
            seen_comments.add(comment_id)
            comment = comments.get(comment_id, {})
            if comment_id not in comments:
                warnings.append("comments_part_missing_or_incomplete")
            elif not (comment.get("text") or "").strip() and not comment.get("has_nested"):
                # 批注正文为空（且没有仅由嵌套修订承载的正文）时必须显式告警，
                # 不能让一条看似干净的 comment 行掩盖空正文。
                warnings.append(f"empty_comment_body:comment_id={comment_id}")
            clause_no, section_no = _paragraph_hints(element, parents)
            events.append(
                {
                    "kind": "comment",
                    "text_before": None,
                    "text_after": comment.get("text") or "",
                    "comment_id": comment_id,
                    "comment_author": comment.get("author"),
                    "comment_date": comment.get("date"),
                    "author": comment.get("author"),
                    "authored_at": comment.get("date"),
                    "element": local,
                    "order": order,
                    "location": {
                        "source": _DOCUMENT_PART,
                        "element": f"w:{local}",
                        "comment_id": comment_id,
                        "order": order,
                    },
                    "clause_no": clause_no,
                    "section_no": section_no,
                }
            )
            continue

        if local in _UNPARSED_REASONS:
            reason = _UNPARSED_REASONS[local]
            # 无法归类的结构一律留档（包括不含文本的结束标记），保证结构
            # 不会在解析中静默消失；重叠用 reason 码显式告警。
            range_marker = local.endswith("RangeStart") or local.endswith("RangeEnd")
            if range_marker:
                # 范围标记常是文件中唯一的修订痕迹：即使没有可见文本也必须
                # 留下行 + 告警，不能让只有范围标记的 DOCX 显示成 0/0。
                warnings.append(f"unparsed_range:{reason}:w:{local}")
            parent_side = next(
                (
                    _revision_side(parent_local)
                    for parent_local in parent_locals
                    if parent_local in _SUPPORTED
                ),
                None,
            )
            text = _node_text(element, side=parent_side) if parent_side else _node_text(element)
            overlapping = [
                parent_local
                for parent_local in parent_locals
                if parent_local in _SUPPORTED
                or parent_local in _UNPARSED_REASONS
                or _is_revision_container(parent_local)
            ]
            for parent_local in overlapping:
                warnings.append(f"overlap:{parent_local}:{local}")
            clause_no, section_no = _paragraph_hints(element, parents)
            events.append(
                {
                    "kind": "unparsed",
                    "text_before": text or None,
                    "text_after": None,
                    "author": (element.get(_w("author")) or "").strip() or None,
                    "authored_at": _xml_date(element.get(_w("date"))),
                    "unparsed_reason": [reason],
                    "element": local,
                    "order": order,
                    "location": {
                        "source": _DOCUMENT_PART,
                        "element": f"w:{local}",
                        "order": order,
                    },
                    "clause_no": clause_no,
                    "section_no": section_no,
                }
            )
            continue

        if local == "nestedRevision" or any(
            _local_name(child.tag) in _SUPPORTED for child in element
        ):
            # 兜底：Word 的 w:customXml 等容器里可能套着修订，上面的遍历会
            # 为内层修订单独建行；这里只在确属嵌套容器而无专属原因码时补一行。
            if local not in _UNPARSED_REASONS and local in {"nestedRevision"}:
                warnings.append(f"overlap:{local}:revision")
                events.append(
                    {
                        "kind": "unparsed",
                        "text_before": _node_text(element) or None,
                        "text_after": None,
                        "unparsed_reason": ["nested_revision"],
                        "element": local,
                        "order": order,
                        "location": {
                            "source": _DOCUMENT_PART,
                            "element": f"w:{local}",
                            "order": order,
                        },
                        "clause_no": None,
                        "section_no": None,
                    }
                )

    # 字段结果（fldSimple 与标准 fldChar 复杂字段）显式留档，文本只在这里出现一次。
    if field_results:
        warnings.append(f"field_result_revision:fields={len(field_results)}")
        for anchor, result_text in field_results:
            events.append(
                {
                    "kind": "unparsed",
                    "text_before": result_text or None,
                    "text_after": None,
                    "unparsed_reason": ["field_result_revision"],
                    "element": _local_name(anchor.tag),
                    "order": document_order.get(id(anchor), 0),
                    "location": {
                        "source": _DOCUMENT_PART,
                        "element": f"w:{_local_name(anchor.tag)}",
                        "field_result": True,
                    },
                }
            )

    # 批注正文里若还有修订结构，也显式留档，绝不静默丢掉。
    # 嵌套修订各自保留自己的方向（插入/删除/未解析），同时保留 comment_id，
    # 批注正文只承载不属于修订结构的可见文本，父子两遍不重复计数。
    if comments_root is not None:
        for comment_element in comments_root.iter():
            if not _is(comment_element.tag, "comment"):
                continue
            comment_id = (comment_element.get(_w("id")) or "").strip()[:64]
            comment_skip_ids = _descendant_revisions(comment_element)
            comment_field_skip_ids, _ = _field_elements(comment_element)
            comment_skip_ids.update(comment_field_skip_ids)
            for node in comment_element.iter():
                local = _local_name(node.tag)
                if node is comment_element or local not in _SUPPORTED:
                    continue
                reason_key = _SUPPORTED[local]
                reason = _COMMENT_REASONS.get(reason_key, f"comment_{reason_key}")
                warnings.append(
                    f"comment_nested_revision:comment_id={comment_id or 'missing'}:w:{local}"
                )
                node_skip = set(comment_skip_ids)
                node_skip.discard(id(node))
                node_skip.update(_descendant_revisions(node))
                node_text = _node_text(node, side=reason_key, skip_ids=node_skip)
                events.append(
                    {
                        "kind": reason_key,  # insertion/deletion 保持事实行方向
                        "text_before": node_text or None if reason_key == "deletion" else None,
                        "text_after": node_text or None if reason_key == "insertion" else None,
                        "author": (node.get(_w("author")) or "").strip() or None,
                        "authored_at": _xml_date(node.get(_w("date"))),
                        "comment_id": comment_id or None,
                        "unparsed_reason": [reason],
                        "element": local,
                        "order": order + 1,
                        "location": {
                            "source": _COMMENTS_PART,
                            "element": f"w:{local}",
                            "comment_id": comment_id or None,
                        },
                        "clause_no": None,
                        "section_no": None,
                    }
                )

    events.sort(key=lambda event: (event.get("order", 0),))
    capped = len(events) > MAX_REDLINE_ROWS
    if capped:
        warnings.append(f"row_cap_exceeded:parsed={len(events)};kept={MAX_REDLINE_ROWS}")
        events = events[:MAX_REDLINE_ROWS]

    rows: list[dict[str, Any]] = []
    for ordinal, event in enumerate(events, start=1):
        kind = event["kind"]
        if kind == "comment_revision":
            revision_type = "unparsed"
        else:
            revision_type = kind
        before = event.get("text_before")
        after = event.get("text_after")
        for label, value in (("before", before), ("after", after)):
            if value is not None and len(value) > MAX_REDLINE_TEXT_CHARS:
                warnings.append(f"text_cap_exceeded:ordinal={ordinal};field={label}")
                if label == "before":
                    before = value[:MAX_REDLINE_TEXT_CHARS]
                else:
                    after = value[:MAX_REDLINE_TEXT_CHARS]
        row: dict[str, Any] = {
            "ordinal": ordinal,
            "revision_type": revision_type,
            "pair_key": None,
            "before_text": before,
            "after_text": after,
            "before_sha256": _sha256(before) if before is not None else None,
            "after_sha256": _sha256(after) if after is not None else None,
            "author": _bounded(event.get("author"), 200),
            "authored_at": event.get("authored_at"),
            "comment_id": _bounded(event.get("comment_id"), 64),
            "comment_author": _bounded(event.get("comment_author"), 200),
            "comment_date": event.get("comment_date"),
            "location": event.get("location") or {},
            "clause_no": event.get("clause_no"),
            "section_no": event.get("section_no"),
            "unparsed_reason": event.get("unparsed_reason"),
            "text_provenance": REVIEW_REDLINE_TEXT_PROVENANCE,
        }
        rows.append(row)

    # 相邻 del→ins 配对：两条都保留，只共享稳定 pair_key，绝不合并成一行。
    for index in range(len(rows) - 1):
        left, right = rows[index], rows[index + 1]
        if left["revision_type"] == "deletion" and right["revision_type"] == "insertion":
            pair_key = f"del-ins-{left['ordinal']}-{right['ordinal']}"
            left["pair_key"] = pair_key
            right["pair_key"] = pair_key

    unparsed_count = sum(row["revision_type"] == "unparsed" for row in rows)
    comment_count = sum(row["revision_type"] == "comment" for row in rows)
    revision_count = sum(row["revision_type"] in ("insertion", "deletion") for row in rows)
    # 稳定去重并保持首次出现顺序（同一结构可能触发多次重叠告警）。
    warnings = list(dict.fromkeys(warnings))
    partial = capped or bool(warnings) or unparsed_count > 0
    status = "partial" if partial else "parsed"
    return RedlineParseResult(
        submission_status=status,
        revision_count=revision_count,
        comment_count=comment_count,
        unparsed_count=unparsed_count,
        parse_warnings=warnings,
        rows=rows,
    )


# ── 数据库服务 ────────────────────────────────────────────────────────


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _assert_scope(case: ReviewCase, current_user: User) -> None:
    """服务层复核租户边界：端点已鉴权，这里防内部调用绕过。

    超管与 ``review_case_service.get_case_for_user`` 保持一致：不受组织
    相等性约束；普通用户仍只可操作本组织案件。
    """
    if getattr(current_user, "is_superuser", False):
        return
    org_id = get_user_org_id(current_user)
    if org_id is not None and org_id != case.organization_id:
        raise NotFoundError("核验案件不存在", resource="review_case", resource_id=case.id)


def _assert_not_archived(case: ReviewCase) -> None:
    if case.archived_at is not None:
        raise ConflictError(
            "案件已归档，不能再写入红线台账",
            error_code="REVIEW_CASE_ARCHIVED",
            detail={"case_id": case.id},
        )


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _submission_summary(submission: ReviewRedlineSubmission) -> dict[str, Any]:
    return {
        "id": submission.id,
        "case_id": submission.case_id,
        "sequence_no": submission.sequence_no,
        "filename": submission.filename,
        "file_sha256": submission.file_sha256,
        "file_size": submission.file_size,
        "parser_version": submission.parser_version,
        "status": submission.status,
        "revision_count": submission.revision_count,
        "comment_count": submission.comment_count,
        "unparsed_count": submission.unparsed_count,
        "parse_warnings": list(submission.parse_warnings or []),
        "uploaded_by": submission.uploaded_by,
        "created_at": _iso(submission.created_at),
    }


def _row_summary(row: ReviewRedlineRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "submission_id": row.submission_id,
        "case_id": row.case_id,
        "ordinal": row.ordinal,
        "revision_type": row.revision_type,
        "pair_key": row.pair_key,
        "before_text": row.before_text,
        "after_text": row.after_text,
        "before_sha256": row.before_sha256,
        "after_sha256": row.after_sha256,
        "author": row.author,
        "authored_at": _iso(row.authored_at),
        "comment_id": row.comment_id,
        "comment_author": row.comment_author,
        "comment_date": _iso(row.comment_date),
        "location": dict(row.location or {}),
        "clause_no": list(row.clause_no) if row.clause_no else None,
        "section_no": list(row.section_no) if row.section_no else None,
        "unparsed_reason": list(row.unparsed_reason) if row.unparsed_reason else None,
        "text_provenance": row.text_provenance,
        "item_id": row.item_id,
        "evidence_id": row.evidence_id,
        "disposition": row.disposition,
        "note": row.note,
        "disposition_by": row.disposition_by,
        "disposition_at": _iso(row.disposition_at),
        "lock_version": row.lock_version,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


async def _lock_case(session: AsyncSession, case: ReviewCase) -> ReviewCase:
    """当前读锁定案件行；先 commit 结束旧 read view（MySQL RR 必需）。"""
    case_id = case.id
    await session.commit()
    fresh = (
        await session.execute(select(ReviewCase).where(ReviewCase.id == case_id).with_for_update())
    ).scalar_one_or_none()
    if fresh is None:
        raise NotFoundError("核验案件不存在", resource="review_case", resource_id=case_id)
    await session.refresh(fresh)
    return fresh


def _safe_filename(filename: str) -> str:
    cleaned = (filename or "redlines.docx").replace("\\", "/").split("/")[-1].strip()
    return (cleaned or "redlines.docx")[:255]


async def upload_submission(
    session: AsyncSession,
    *,
    case: ReviewCase,
    current_user: User,
    filename: str,
    data: bytes,
    storage_object_name: str | None = None,
) -> dict[str, Any]:
    """上传并解析对方 DOCX：先提交 DB 快照与审计，再写 MinIO 对象。

    无删除接口，因此绝不在 DB 提交前写对象：否则 409/IntegrityError 会留下
    无引用对象。顺序固定为 ①锁定案件并预留批次/行 + 上传审计（
    status=parsed|partial，对象名在提交前已确定）→ ②提交 DB → ③上传对象。
    任何 409/写失败都发生在 ①之前或 ①之内，对象从未写入；步骤③失败时 DB
    中保留完整引用行与审计（原文可重传修复），且对象上传之后不再有任何
    DB 写入，彻底消除「提交失败留下孤儿对象」的路径。
    """
    _assert_scope(case, current_user)
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValidationError("DOCX 内容必须是字节", field="file")
    payload = bytes(data)
    if len(payload) > MAX_REDLINE_UPLOAD_BYTES:
        raise ValidationError(
            "DOCX 超过上传上限",
            field="file",
            detail={"max_bytes": MAX_REDLINE_UPLOAD_BYTES, "received_bytes": len(payload)},
        )
    # 先解析再占 DB 连接：解析失败不产生任何半写入。
    parsed = parse_redline_docx(payload, filename)
    fresh_case = await _lock_case(session, case)
    _assert_not_archived(fresh_case)

    case_id = fresh_case.id
    org_id = fresh_case.organization_id
    next_sequence = (
        await session.execute(
            select(func.max(ReviewRedlineSubmission.sequence_no)).where(
                ReviewRedlineSubmission.case_id == case_id
            )
        )
    ).scalar() or 0
    submission_id = str(uuid.uuid4())
    object_name = storage_object_name or f"redlines/{org_id}/{case_id}/{submission_id}.docx"
    file_sha256 = hashlib.sha256(payload).hexdigest()
    submission = ReviewRedlineSubmission(
        id=submission_id,
        organization_id=org_id,
        case_id=case_id,
        sequence_no=int(next_sequence) + 1,
        filename=_safe_filename(filename),
        file_sha256=file_sha256,
        file_size=len(payload),
        storage_object_name=object_name,
        parser_version=REDLINE_PARSER_VERSION,
        status=parsed.submission_status,
        revision_count=parsed.revision_count,
        comment_count=parsed.comment_count,
        unparsed_count=parsed.unparsed_count,
        parse_warnings=list(parsed.parse_warnings),
        uploaded_by=current_user.id,
    )
    session.add(submission)
    for parsed_row in parsed.rows:
        fields = {key: value for key, value in parsed_row.items() if key in _ROW_FIELDS}
        session.add(
            ReviewRedlineRow(
                id=str(uuid.uuid4()),
                submission_id=submission_id,
                case_id=case_id,
                organization_id=org_id,
                disposition="pending",
                lock_version=1,
                **fields,
            )
        )

    # M2：上传审计与批次/行在同一事务内提交；只含 id/哈希前缀，
    # 不含条款正文、作者姓名或其他原文内容。
    session.add(
        UserActivityLog(
            user_id=current_user.id,
            action="review_redline_upload",
            target_type="review_case",
            target_id=case_id,
            detail=_canonical_json(
                {
                    "organization_id": org_id,
                    "user_id": current_user.id,
                    "case_id": case_id,
                    "submission_id": submission_id,
                    "sequence_no": submission.sequence_no,
                    "file_sha256_prefix": file_sha256[:16],
                    "file_size": len(payload),
                    "status": submission.status,
                }
            ),
        )
    )
    try:
        # ① 先持久化 DB 快照（含确定的对象名）与审计并提交；此时对象尚未写入。
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "红线台账批次冲突，请重试",
            error_code="REVIEW_REDLINE_CONFLICT",
            detail={"case_id": case_id},
        ) from exc
    except Exception:
        await session.rollback()
        raise

    try:
        # ② DB 提交成功后才写对象：409/回滚路径永远到不了这里，无孤儿对象。
        # 对象上传之后没有任何 DB 写入，故不存在「对象已写、提交失败」的窗口。
        await asyncio.to_thread(
            minio_client.put_object,
            object_name=object_name,
            data=BytesIO(payload),
            length=len(payload),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    except Exception as exc:
        # 对象写入失败：DB 行已提交且引用该对象名，重传同一对象即可修复；
        # 绝不删除对象（系统无删除策略），也不回滚已提交的引用行。
        await session.rollback()
        raise ServiceUnavailableError(
            "红线原文存储失败，请稍后重试",
            error_code="REVIEW_REDLINE_STORAGE_UNAVAILABLE",
            detail={"case_id": case_id},
        ) from exc
    await session.refresh(submission)
    return _submission_summary(submission)


async def list_submissions(session: AsyncSession, *, case: ReviewCase) -> dict[str, Any]:
    """列出案件下所有批次摘要（不含任何条款正文）。"""
    submissions = (
        (
            await session.execute(
                select(ReviewRedlineSubmission)
                .where(
                    ReviewRedlineSubmission.case_id == case.id,
                    ReviewRedlineSubmission.organization_id == case.organization_id,
                )
                .order_by(ReviewRedlineSubmission.sequence_no.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [_submission_summary(item) for item in submissions],
        "total": len(submissions),
    }


async def get_submission(
    session: AsyncSession, *, case: ReviewCase, submission_id: str
) -> ReviewRedlineSubmission:
    submission = await session.get(ReviewRedlineSubmission, submission_id)
    if (
        submission is None
        or submission.case_id != case.id
        or submission.organization_id != case.organization_id
    ):
        raise NotFoundError(
            "红线台账批次不存在", resource="review_redline_submission", resource_id=submission_id
        )
    return submission


async def get_row(
    session: AsyncSession, *, case: ReviewCase, submission_id: str, row_id: str
) -> ReviewRedlineRow:
    submission = await get_submission(session, case=case, submission_id=submission_id)
    row = await session.get(ReviewRedlineRow, row_id)
    if (
        row is None
        or row.submission_id != submission.id
        or row.case_id != case.id
        or row.organization_id != case.organization_id
    ):
        raise NotFoundError("红线台账行不存在", resource="review_redline_row", resource_id=row_id)
    return row


async def get_ledger(
    session: AsyncSession,
    *,
    case: ReviewCase,
    submission_id: str,
    revision_type: str | None = None,
    disposition: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """分页读取台账行；counts/has_unparsed 与 items、total 同口径（受过滤条件影响）。

    parse_warnings 与 has_truncation 来自批次解析快照，描述原文解析时是否
    达到文本/行数/深度上限，不受当前页过滤器影响。
    """
    submission = await get_submission(session, case=case, submission_id=submission_id)
    if revision_type is not None and revision_type not in REVIEW_REDLINE_ROW_TYPES:
        raise ValidationError("未知修订类型", field="revision_type")
    if disposition is not None and disposition not in REVIEW_REDLINE_DISPOSITIONS:
        raise ValidationError("未知处置状态", field="disposition")
    if page < 1 or page_size < 1 or page_size > 100:
        raise ValidationError("分页参数无效", field="page")

    conditions = [
        ReviewRedlineRow.submission_id == submission.id,
        ReviewRedlineRow.case_id == case.id,
        ReviewRedlineRow.organization_id == case.organization_id,
    ]
    if revision_type is not None:
        conditions.append(ReviewRedlineRow.revision_type == revision_type)
    if disposition is not None:
        conditions.append(ReviewRedlineRow.disposition == disposition)

    total = int(
        (await session.execute(select(func.count(ReviewRedlineRow.id)).where(*conditions))).scalar()
        or 0
    )
    rows = (
        (
            await session.execute(
                select(ReviewRedlineRow)
                .where(*conditions)
                .order_by(ReviewRedlineRow.ordinal)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    grouped = dict(
        (
            await session.execute(
                select(ReviewRedlineRow.revision_type, func.count(ReviewRedlineRow.id))
                .where(*conditions)
                .group_by(ReviewRedlineRow.revision_type)
            )
        ).all()
    )
    counts = {
        "revision": int(grouped.get("insertion", 0)) + int(grouped.get("deletion", 0)),
        "comment": int(grouped.get("comment", 0)),
        "unparsed": int(grouped.get("unparsed", 0)),
    }
    warnings = list(submission.parse_warnings or [])
    return {
        "items": [_row_summary(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "counts": counts,
        "disclaimer": REDLINE_DISCLAIMER,
        "has_unparsed": counts["unparsed"] > 0,
        "parse_warnings": warnings,
        "has_truncation": any(
            warning.startswith(("row_cap_exceeded", "text_cap_exceeded", "xml_depth_cap_exceeded"))
            for warning in warnings
        ),
    }


def _validate_id(value: str, field: str) -> str:
    if len(value) > 36 or not _UUID_RE.fullmatch(value):
        raise ValidationError(f"{field} 不是有效 id", field=field)
    return value


async def update_disposition(
    session: AsyncSession,
    *,
    case: ReviewCase,
    current_user: User,
    submission_id: str,
    row_id: str,
    update: RedlineDispositionUpdate,
) -> dict[str, Any]:
    """人工处置单行：乐观锁 + 同事务审计；原文、哈希与其他案件对象不变。"""
    _assert_scope(case, current_user)
    case_id = case.id
    submission = await get_submission(session, case=case, submission_id=submission_id)
    row_before = await get_row(session, case=case, submission_id=submission_id, row_id=row_id)
    if update.disposition not in REVIEW_REDLINE_DISPOSITIONS:
        raise ValidationError("未知处置状态", field="disposition")
    if update.evidence_id is not None and update.item_id is None:
        raise ValidationError("指定证据时必须同时指定核验项", field="evidence_id")

    # 使用本次提交的 item 作为 evidence 绑定依据；不读旧行的 item_id。
    submitted_item_id = (
        _validate_id(update.item_id, "item_id") if update.item_id is not None else None
    )
    submitted_evidence_id = (
        _validate_id(update.evidence_id, "evidence_id") if update.evidence_id is not None else None
    )
    if submitted_item_id is not None:
        item = await session.get(ReviewItem, submitted_item_id)
        if item is None or item.case_id != case_id:
            raise NotFoundError(
                "核验项不存在", resource="review_item", resource_id=submitted_item_id
            )
    if submitted_evidence_id is not None:
        # 证据不存在必须 404；不能把悬空 id 静默写进台账。
        evidence = await session.get(ReviewEvidence, submitted_evidence_id)
        binding_item_id = submitted_item_id or row_before.item_id
        if (
            evidence is None
            or evidence.case_id != case_id
            or binding_item_id is None
            or evidence.item_id != binding_item_id
        ):
            raise NotFoundError(
                "证据不存在", resource="review_evidence", resource_id=submitted_evidence_id
            )

    # 结束只读视图后当前读锁定案件行（归档检查必须在锁内）；案件锁持有到
    # 最终 commit，防止「载入时未归档、写入前被归档」的窄竞态；MySQL RR 下
    # 必须先提交旧视图。行级乐观锁不依赖 ORM 实例状态：下面用条件 UPDATE
    # 原子比较 lock_version 并据此更新，避免 SQLAlchemy 身份映射（
    # expire_on_commit=False）把 SELECT FOR UPDATE 的旧实例重新返回，
    # 导致过期实例覆盖他人的写入而不报 409。
    fresh_case = await _lock_case(session, case)
    _assert_not_archived(fresh_case)
    submission = await get_submission(session, case=fresh_case, submission_id=submission_id)
    # 只校验行是否存在/归属；版本判定完全交给下面的条件 UPDATE。
    exists = (
        await session.execute(
            select(ReviewRedlineRow.id).where(
                ReviewRedlineRow.id == row_id,
                ReviewRedlineRow.submission_id == submission.id,
                ReviewRedlineRow.case_id == fresh_case.id,
                ReviewRedlineRow.organization_id == fresh_case.organization_id,
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        raise NotFoundError("红线台账行不存在", resource="review_redline_row", resource_id=row_id)

    expected_version = update.expected_lock_version
    new_version = expected_version + 1
    disposition_at = datetime.now(UTC)
    result = await session.execute(
        sa_update(ReviewRedlineRow)
        .where(
            ReviewRedlineRow.id == row_id,
            ReviewRedlineRow.submission_id == submission.id,
            ReviewRedlineRow.case_id == fresh_case.id,
            ReviewRedlineRow.organization_id == fresh_case.organization_id,
            ReviewRedlineRow.lock_version == expected_version,
        )
        .values(
            disposition=update.disposition,
            note=update.note,
            # 无条件赋值：显式 null 表示解除关联，不能沿用旧值。
            item_id=submitted_item_id,
            evidence_id=submitted_evidence_id,
            disposition_by=current_user.id,
            disposition_at=disposition_at,
            lock_version=new_version,
        )
    )
    if result.rowcount != 1:
        current = (
            await session.execute(
                select(ReviewRedlineRow.lock_version).where(ReviewRedlineRow.id == row_id)
            )
        ).scalar_one_or_none()
        await session.rollback()
        raise ConflictError(
            "红线台账行已被其他人更新，请刷新后重试",
            error_code="REVIEW_REDLINE_CONFLICT",
            detail={
                "expected_lock_version": expected_version,
                "current_lock_version": current,
            },
        )

    session.add(
        UserActivityLog(
            user_id=current_user.id,
            action="review_redline_disposition",
            target_type="review_case",
            target_id=fresh_case.id,
            detail=_canonical_json(
                {
                    "organization_id": fresh_case.organization_id,
                    "user_id": current_user.id,
                    "case_id": case_id,
                    "submission_id": submission.id,
                    "row_id": row_id,
                    "disposition": update.disposition,
                    "lock_version": new_version,
                }
            ),
        )
    )
    await session.commit()
    # 条件 UPDATE 默认同步身份映射；再用 populate_existing 读取，确保返回
    # 的一定是数据库真值。不能用 expire_all()：那会让调用方持有的 user/case
    # 实例过期，后续属性读取在异步上下文里触发 MissingGreenlet。
    return await _get_row_summary_by_id(session, row_id)


async def _get_row_summary_by_id(session: AsyncSession, row_id: str) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                select(ReviewRedlineRow)
                .where(ReviewRedlineRow.id == row_id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )
    return _row_summary(row)
