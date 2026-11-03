"""确定性条款抽取与 V1↔V2 对齐（合同核验场景 Batch 3）。

口径（与 ``_scratch/orchestration/audit_contract_review_gap_20261027.md`` 对齐）：

- 只从 ``DocumentChunk.original_text``（``text_provenance=parser_original``）读取；
  文档未索引/无分块/混有 legacy 时分析显式拒绝，绝不把不完整输入当全量；
- 只抽取四个固定核验项的**文本事实**：期限值/单位（工作日/自然日/月）、起算
  事件、验收/发票前提、例外/暂缓/另有约定/交叉引用；无法确定时标
  ``ambiguous``/``not_found``，绝不猜值；
- 抽取前统一做版式归一（NFKC、去空白、去页码噪声），但证据 quote 始终保存
  原始原文；日期片段（2025年3月/3月15日）与保修/质保/审核等非付款期限
  一律不作为账期候选；
- 对齐 = 文本事实 + 归一化主条款文本哈希：事实不同 → modified；事实相同但
  文本变化（否定/主体/限定语）→ modified；事实与文本相同、条号变化 →
  renumbered；完全相同 → unchanged；无法判定/扫描截断 → unidentified；
- 条款/事件的先后顺序不产生事实差异（按集合比较，仅顺序变化 → unchanged）；
  moved 类型 v1 不产出；排除语境只在期限所在分句及其相邻分句的付款回退中
  生效（「付款方逾期，30 天内应支付货款」这类跨分句排除属已知边界）；交货动词
  与「付款后 N 天内交货」类复合语序、宽限期/质保金期限同句属 v1 已登记
  边界（见 tests 中 xfail / 保守 ambiguous 用例）；
- 纯规则实现：不调用 LLM、不访问网络、不依赖 ES；同一输入重复运行结果一致；
- 主条款以 candidate 证据快照落库（已存在的同一 item/side/chunk 不重复写入，
  不触碰人工已选/角色）。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.review import ReviewCase, ReviewItem
from app.rag.source_provenance import SourceProvenanceUnavailableError
from app.services.clause_refs import extract_clause_fields
from app.services.review_case_service import case_detail, ensure_candidate_evidence
from app.services.review_document_service import (
    MAX_EVIDENCE_SCAN_CHUNKS,
    chunk_original_text,
    get_review_meta,
    iter_chunks,
)

EXTRACTOR_VERSION = 1
ITEM_KEYS = ("payment_term", "start_condition", "acceptance_invoice", "exception")
# 人工/导出可判定的对齐结果（moved 需要版式位置语义，v1 不产出）。
CHANGE_TYPES = (
    "unchanged",
    "modified",
    "added",
    "removed",
    "renumbered",
    "unidentified",
)

PAYMENT_KEYWORDS = ("付款", "支付", "账期", "价款", "货款", "结算")
# 期限匹配必须离付款锚点比这些「非付款期限」语境更近（保修期/日期签署等）。
NON_PAYMENT_CONTEXT = (
    "保修",
    "质保",
    "审核",
    "有效期",
    "保密期",
    "到期",
    "保证金",
    "押金",
    "逾期",
    "超过",
    "延迟",
    "超期",
    "违约",
    "滞纳",
    "罚息",
    "交货期",
    "交付期",
    "工期",
    "供货期",
    "装运期",
    "服务期",
    "质量保证",
    "供货周期",
    "合同期限",
    "合作期",
    "试用期",
    "租赁期",
)
# 「天」按自然日；单独「日」无法判定自然日/工作日 → ambiguous。
DURATION_RE = re.compile(r"(\d+)\s*(个)?\s*(工作日|自然日|日历日|天|日)")
MONTH_RE = re.compile(r"(\d+)\s*个\s*月")
# 中文数字月份（三个月 / 六个月）
CN_MONTH_RE = re.compile(r"([一二两三四五六七八九十百零〇]+)\s*个\s*月")
# 交付类事件：期限归属与屏蔽时视作“非付款”锚点。
DELIVERY_TERMS = ("交付", "交货", "供货", "安装", "发货")
# 中文数字期限（三十日 / 六十个工作日 / 一百零五天）
CN_DURATION_RE = re.compile(
    r"([一二两三四五六七八九十百零〇]+)\s*(个)?\s*(工作日|自然日|日历日|天|日)"
)
_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _cn_to_int(text: str) -> int | None:
    """中文数字转整数（覆盖 1–999 的常见合同写法）。"""
    text = text.replace("零", "").replace("〇", "")
    if not text:
        return None
    if "百" in text:
        head, _, tail = text.partition("百")
        hundreds = _CN_DIGITS.get(head, 1) if head else 1
        rest = _cn_to_int(tail) if tail else 0
        if hundreds is None or rest is None:
            return None
        return hundreds * 100 + rest
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        units = _CN_DIGITS.get(tail, 0) if tail else 0
        if tens is None or units is None:
            return None
        return tens * 10 + units
    return _CN_DIGITS.get(text)


SENTENCE_SPLIT_RE = re.compile(r"[。；;！!？?\n]+")
FRAGMENT_SPLIT_RE = re.compile(r"[，,、]")
UNIT_MAP = {"工作日": "workday", "自然日": "day", "日历日": "day", "天": "day"}
START_EVENTS = (
    "收到发票",
    "收到对账单",
    "对账无误",
    "对账无异议",
    "验收合格",
    "货物交付",
    "交付验收",
    "签署之日",
    "生效之日",
    "开票",
)
ACCEPTANCE_TERMS = ("验收合格", "对账单", "验收", "发票", "对账")
EXCEPTION_TERMS = (
    "不可抗力",
    "补充协议",
    "另行协商",
    "另行约定",
    "另有约定",
    "暂缓",
    "除外",
    "例外",
)
_EXCEPTION_TERM_PATTERN = "|".join(re.escape(term) for term in EXCEPTION_TERMS)
# 无冒号但标题形态明确：「第九条 例外 …」。付款条款「第三条 付款期限 除不可抗力外…」
# 不会命中（例外词不在条号之后）。
_NO_COLON_EXCEPTION_RE = re.compile(
    rf"^第[一二三四五六七八九十百零〇0-9]+条\s*(?:{_EXCEPTION_TERM_PATTERN})"
    r"(?:[\s:：，,。；;、]|$)"
)
# 「例外情形下的付款 / 不可抗力情况下的付款」：例外词作定语，属付款条款而非
# 独立例外条款。
_EXCEPTION_AS_PAYMENT_HEADING_RE = re.compile(
    rf"^第[一二三四五六七八九十百零〇0-9]+条\s*(?:{_EXCEPTION_TERM_PATTERN})"
    r"\s*(?:情形|情况)?\s*下?的?\s*[及与和、]?\s*"
    r"(?:[应预首分]?期?付款|应?支付|账期|价款|货款|结算)"
)
# 例外词后是标点、紧跟合同主语（「补充协议，甲方应…」）属正文，不是标题。
_EXCEPTION_BODY_SUBJECT_RE = re.compile(
    rf"^第[一二三四五六七八九十百零〇0-9]+条\s*(?:{_EXCEPTION_TERM_PATTERN})"
    r"\s*[，,。；;、]\s*"
    r"(?:[甲乙丙丁戊]方|双方|各方|任何一方|卖方|买方|供方|需方|承包人|发包人|"
    r"供应商|采购人|本合同|本协议)"
)
# 阈值语境：定义「逾期/超过 N 天」的违约条件时，期限不是账期。
THRESHOLD_CONTEXT = ("逾期", "超过", "延迟", "超期", "违约", "滞纳", "罚息")
CROSS_REF_RE = re.compile(
    r"(?:详见|见|依据|根据|按照|参照)(?:本合同|本协议)?"
    r"第[一二三四五六七八九十百零〇0-9]+条"
)
# 付款语境内的显式条款引用：「具体账期按第四条执行」「付款期限详见第五条」。
# 只认期限型短语（账期/付款期限/结算周期…），裸「付款/支付」会吞掉
# 「支付违约金按第九条」「按第四条约定支付货款」这类非期限引用；引用词前的
# 间隔不得跨句读与冒号，避免从条款标题横穿到远端子句。同一引用短语里的
# 并列/范围目标（第四条、第五条，第四条至第六条）整体跟随，不得只取第一个。
_PAYMENT_TERM_PHRASE = (
    r"(?:付款期限|支付期限|付款时间|支付时间|付款日期|支付日期|"
    r"付款节点|支付节点|付款安排|支付安排|结算期限|结算周期|结算时间|账期)"
)
_REF_VERB = r"(?:详见|参见|见|按照|按|依据|依照|根据|参照|以)"
_REF_CLAUSE = r"(?:本合同|本协议)?第[一二三四五六七八九十百零〇0-9]+条"
_REF_SERIES = r"(?:\s*(?:[、,，和及与]|以及|至)\s*第[一二三四五六七八九十百零〇0-9]+条)*"
_PAYMENT_REF_RE = re.compile(
    rf"(?:"
    rf"{_PAYMENT_TERM_PHRASE}[^。；;：:，,]{{0,6}}?{_REF_VERB}{_REF_CLAUSE}{_REF_SERIES}"
    rf"|{_PAYMENT_TERM_PHRASE}[:：]\s*(?:具体|应当|应|须|需|均|可)?\s*"
    rf"{_REF_VERB}{_REF_CLAUSE}{_REF_SERIES}"
    rf")"
)
_CLAUSE_NO_RE = re.compile(r"第([一二三四五六七八九十百零〇0-9]+)条")
_REF_RANGE_RE = re.compile(
    r"第([一二三四五六七八九十百零〇0-9]+)条\s*至\s*第([一二三四五六七八九十百零〇0-9]+)条"
)


@dataclass(frozen=True)
class Clause:
    """条款：text 为版式归一后的抽取文本，raw_text 为证据 quote 原文。"""

    chunk_id: str
    chunk_index: int
    text: str
    raw_text: str
    clause_no: list[int]
    section_no: list[str]
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None


def normalize_clause_text(text: str) -> str:
    """归一化空白/全半角/页码噪声；只服务抽取与文本对比，不改原文。"""
    value = unicodedata.normalize("NFKC", text)
    value = re.sub(r"第\s*\d+\s*页(?:\s*共\s*\d+\s*页)?", "", value)
    return re.sub(r"\s+", "", value)


# 比较文本事实时剥掉行首条号：仅条号变化记 renumbered，不记 modified。
_CLAUSE_NUMBER_PREFIX_RE = re.compile(r"^第[一二三四五六七八九十百零〇0-9]+条")


def _content_sha(clause: Clause) -> str:
    content = _CLAUSE_NUMBER_PREFIX_RE.sub("", clause.text, count=1)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _matched_text_sha(clauses: list[Clause]) -> str:
    """命中条款集合的文本哈希（按集合，条款先后顺序不产生差异）。"""
    unique = {clause.chunk_id: clause for clause in clauses}
    return hashlib.sha256(
        "|".join(sorted(_content_sha(c) for c in unique.values())).encode()
    ).hexdigest()


_MASK = "\u0001"
_CONNECTOR_CHARS = set("并且及与、，,。；; \t\n") | {_MASK}


def _collapse_mask(text: str) -> str:
    out: list[str] = []
    prev_mask = False
    for ch in text:
        if ch in _CONNECTOR_CHARS:
            if not prev_mask:
                out.append(_MASK)
            prev_mask = True
        else:
            out.append(ch)
            prev_mask = False
    return "".join(out)


_HEADING_SPLIT_RE = re.compile(r"[:：]")


def _duration_owned_by_delivery(sentence: str, start: int, end: int) -> bool:
    """期限归属交付类事件时，不作为付款期限。

    「30天内交付货款」中的「交付货款」是付款动词短语，仍属付款；
    「10个工作日内交付，甲方随后付款」的 10 归交付（不作为付款 hit）。
    """
    after = sentence[end:]
    if re.search(r"交付(?:全部|首期|剩余|合同约定)?(?:货款|价款|款项)", after[:12]):
        return False
    delivery_index = min(
        (after.find(term) for term in DELIVERY_TERMS if term in after), default=None
    )
    payment_index = min(
        (after.find(keyword) for keyword in PAYMENT_KEYWORDS if keyword in after), default=None
    )
    if delivery_index is None:
        return False
    return payment_index is None or delivery_index < payment_index


def _mask_payment_durations(text: str) -> str:
    """只屏蔽“最近的语义锚点是付款词”的期限数字。

    依据锚点距离而非整句/整片段：`10个工作日内完成对账并在30天内支付货款` 中
    10 最近的是验收/对账词（保留），30 最近的是“支付”（屏蔽）；跨条款拼接、
    无逗号长句也不会把验收自己的期限一起吞掉。
    """
    matches = (
        list(DURATION_RE.finditer(text))
        + list(MONTH_RE.finditer(text))
        + list(CN_DURATION_RE.finditer(text))
        + list(CN_MONTH_RE.finditer(text))
    )
    mask_spans = sorted(
        {
            (match.start(), match.end())
            for match in matches
            if _nearest_anchor_is_payment(text, match.start(), match.end())
        }
    )
    if not mask_spans:
        return text

    parts: list[str] = []
    cursor = 0
    for start, end in mask_spans:
        if start < cursor:  # 与上一个屏蔽区间重叠（DURATION 已覆盖的情况）
            continue
        parts.append(text[cursor:start])
        parts.append(_MASK)
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


_CONTINUATION_SUBJECTS = (
    "甲方",
    "乙方",
    "丙方",
    "丁方",
    "戊方",
    "双方",
    "各方",
    "任何一方",
    "卖方",
    "买方",
    "供方",
    "需方",
    "承包人",
    "发包人",
    "供应商",
    "采购人",
)

# 连词后接通知/约定类动词时，付款只是被提及而非延续前一片段的谓语。
_CONTINUATION_INTERVENING_VERBS = (
    "通知",
    "告知",
    "提醒",
    "要求",
    "约定",
    "确认",
    "申请",
    "催告",
    "提交",
)


def _payment_continues_after_fragment(text: str, boundary_end: int, sentence_end: int) -> bool:
    """逗号后是否为“并/且/同时支付”式谓语延续。

    「完成对账，并支付货款」的付款期限可以跨逗号归付款；
    「完成验收，随后付款/付款/之后付款/再付款」是新谓语，不得回吸前一片段的期限；
    「完成验收，并由甲方支付货款 / 并提醒付款」连接新主语或通知类动词，
    期限只约束前一片段，同样不得跨逗号归付款。
    """
    tail = text[boundary_end:sentence_end].lstrip()
    if not tail:
        return False
    payment_positions = [tail.find(keyword) for keyword in PAYMENT_KEYWORDS if keyword in tail]
    if not payment_positions:
        return False
    pay_pos = min(payment_positions)
    if any(subject in tail[:pay_pos] for subject in _CONTINUATION_SUBJECTS):
        return False
    if tail[0] in "并且":
        lead = tail[1:pay_pos]
    elif tail.startswith("同时"):
        lead = tail[2:pay_pos]
    else:
        # 连词紧贴付款词（「核对无误并支付」）仍算延续。
        if pay_pos >= 1 and tail[pay_pos - 1] in "并且":
            return True
        return pay_pos >= 2 and tail[pay_pos - 2 : pay_pos] == "同时"
    return not any(verb in lead for verb in _CONTINUATION_INTERVENING_VERBS)


def _nearest_anchor_is_payment(text: str, start: int, end: int) -> bool:
    """判断期限之后的最近语义锚点是否为付款词（方向感知）。

    付款动词常隔“内向乙方/内完成/内安排”几个字（如「30个自然日内向乙方支付」），
    此时期限前紧邻的「验收合格」并不是它的归属；只有期限之后存在锚点时才由其
    决定归属，之后没有锚点才回看之前（覆盖「10天内起算」这类后置定义）。
    """
    # 连谓结构：期限之后、下一个期限之前若出现付款动词（且没有交付类词），
    # 该期限归付款（「30天内完成对账并支付」的 30 是付款期限）。
    next_starts: list[int] = []
    for pattern in (DURATION_RE, MONTH_RE, CN_DURATION_RE, CN_MONTH_RE):
        for match in pattern.finditer(text):
            if match.start() >= end:
                next_starts.append(match.start())
    # 付款动词必须与期限同句：后一句的「付款」不得把前一句的验收期限判成付款。
    sentence_end = len(text)
    boundary = SENTENCE_SPLIT_RE.search(text, end)
    if boundary:
        sentence_end = boundary.start()
    # 连谓付款动词必须与期限同片段：逗号/顿号后的「随后付款」不得回吸前一片段。
    fragment_end = len(text)
    fragment_boundary = FRAGMENT_SPLIT_RE.search(text, end)
    if fragment_boundary:
        fragment_end = fragment_boundary.start()
        if _payment_continues_after_fragment(text, fragment_boundary.end(), sentence_end):
            fragment_end = sentence_end
    span_end = min(next_starts + [sentence_end, fragment_end])
    span = text[end:span_end]
    if any(keyword in span for keyword in PAYMENT_KEYWORDS) and not any(
        delivery in span for delivery in DELIVERY_TERMS
    ):
        return True

    after: list[tuple[int, int]] = []
    before: list[tuple[int, int]] = []
    for kind, terms in (
        (0, PAYMENT_KEYWORDS),
        (1, START_EVENTS + ACCEPTANCE_TERMS + DELIVERY_TERMS),
    ):
        for term in terms:
            index = text.find(term)
            while index != -1:
                term_end = index + len(term)
                if index >= end:
                    after.append((index - end, kind))
                elif term_end <= start:
                    before.append((start - term_end, kind))
                index = text.find(term, term_end)
    pool = after if after else before
    if not pool:
        return False
    best = min(pool)  # 距离相同按付款优先（kind=0）
    return best[1] == 0


def _is_exception_article(clause: Clause) -> bool:
    """识别独立的例外条款：标题必须是例外主题（不含付款词），允许无冒号形态。

    - 有标题分隔符：「第九条 例外：…」标题含例外词且不含付款词；
    - 无冒号但标题形态明确：「第九条 例外 …」；
    - 「第三条 付款期限 除不可抗力外…」不命中（例外词不在条号之后）。
    """
    raw = clause.raw_text or clause.text
    # 先认明确的标题形态：「第X条 例外<分隔>…」。即便正文后面还有冒号（如
    # 「例外 因不可抗力导致付款延迟的：顺延30天」）也仍是独立例外条款；
    # 例外词作付款定语（含 及/与/和 连接）或分隔符后紧跟合同主语的属正文。
    if (
        _NO_COLON_EXCEPTION_RE.match(raw)
        and not _EXCEPTION_AS_PAYMENT_HEADING_RE.match(raw)
        and not _EXCEPTION_BODY_SUBJECT_RE.match(raw)
    ):
        return True
    parts = _HEADING_SPLIT_RE.split(clause.text, maxsplit=1)
    if len(parts) >= 2:
        heading = parts[0].split("\n", 1)[0][:40]
        if not any(keyword in heading for keyword in PAYMENT_KEYWORDS) and any(
            term in heading for term in EXCEPTION_TERMS
        ):
            return True
    return False


def _mask_item_noise(text: str, item_key: str) -> str:
    """屏蔽“属于其它核验项”的词元，避免共享条款的文本哈希互相污染。"""
    if item_key == "payment_term":
        for term in sorted(START_EVENTS + ACCEPTANCE_TERMS, key=len, reverse=True):
            text = text.replace(term, _MASK)
        return _collapse_mask(text)
    if item_key in ("start_condition", "acceptance_invoice"):
        return _mask_payment_durations(text)
    # exception：不屏蔽任何期限——“顺延30天→15天”本身就是例外项的变化。
    return text


def _content_sha_for(clause: Clause, item_key: str) -> str:
    content = _CLAUSE_NUMBER_PREFIX_RE.sub("", clause.text, count=1)
    content = _mask_item_noise(content, item_key)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _matched_text_sha_for(clauses: list[Clause], item_key: str) -> str:
    unique = {clause.chunk_id: clause for clause in clauses}
    return hashlib.sha256(
        "|".join(sorted(_content_sha_for(c, item_key) for c in unique.values())).encode()
    ).hexdigest()


def _clause_no_union(clauses: list[Clause]) -> list[int]:
    return sorted({number for clause in clauses for number in clause.clause_no})


def _locator_nos(facts: dict[str, Any]) -> list[int]:
    """对齐用的条款号集合：只取参与 payload 的条款（证据展示用的 union 另存）。"""
    return (
        facts.get("payload_clause_no")
        or facts.get("clause_no_union")
        or facts.get("clause_no")
        or []
    )


def _dedupe_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    """同一条款里取最长命中词，避免「对账单」同时命中「对账」。"""
    matched = [term for term in terms if term in text]
    result: list[str] = []
    for term in matched:
        if any(term != other and term in other for other in matched):
            continue
        if term not in result:
            result.append(term)
    return result


def _primary_locator(clause: Clause) -> dict[str, Any]:
    return {
        "chunk_id": clause.chunk_id,
        "chunk_index": clause.chunk_index,
        "clause_no": list(clause.clause_no),
        "section_no": list(clause.section_no),
        "page_number": clause.page_number,
    }


def _iter_sentences(text: str):
    start = 0
    for match in SENTENCE_SPLIT_RE.finditer(text):
        yield text[start : match.start()]
        start = match.end()
    yield text[start:]


def _anchor_distance(sentence: str, start: int, end: int, anchors: tuple[str, ...]) -> int | None:
    """最近的锚点到 [start, end) 的字符距离；锚点在数字前后都算。"""
    best: int | None = None
    for anchor in anchors:
        index = sentence.find(anchor)
        while index != -1:
            anchor_start, anchor_end = index, index + len(anchor)
            if anchor_end <= start:
                distance = start - anchor_end
            elif anchor_start >= end:
                distance = anchor_start - end
            else:  # 理论上不会与纯数字区间重叠
                distance = 0
            if best is None or distance < best:
                best = distance
            index = sentence.find(anchor, index + 1)
    return best


def _iter_fragments(sentence: str):
    """按逗号/顿号切分句内片段，yield (片段文本, 起始偏移)。"""
    start = 0
    for match in FRAGMENT_SPLIT_RE.finditer(sentence):
        yield sentence[start : match.start()], start
        start = match.end()
    yield sentence[start:], start


def _adjacent_to_exclusion(fragment: str, start: int, end: int) -> bool:
    for exclusion in NON_PAYMENT_CONTEXT:
        index = fragment.find(exclusion)
        while index != -1:
            exclusion_start, exclusion_end = index, index + len(exclusion)
            if start <= exclusion_end + 1 and exclusion_start <= end + 1:
                return True
            index = fragment.find(exclusion, index + 1)
    return False


def _payment_anchor_distance(fragment: str, start: int, end: int) -> int | None:
    """最近的付款锚点距离。

    与排除锚点相邻的付款词不算（「逾期付款」）；与已跳过付款词相邻的复合词
    同样跳过（「逾期支付货款」里的「货款」），避免复合语境被任一近义词带回。
    """
    spans: list[tuple[int, int]] = []
    for anchor in PAYMENT_KEYWORDS:
        index = fragment.find(anchor)
        while index != -1:
            spans.append((index, index + len(anchor)))
            index = fragment.find(anchor, index + 1)
    skipped = {(s0, s1) for s0, s1 in spans if _adjacent_to_exclusion(fragment, s0, s1)}
    changed = True
    while changed:
        changed = False
        for s0, s1 in spans:
            if (s0, s1) in skipped:
                continue
            if any(s0 <= k1 + 1 and k0 <= s1 + 1 for k0, k1 in skipped):
                skipped.add((s0, s1))
                changed = True
    best: int | None = None
    for s0, s1 in spans:
        if (s0, s1) in skipped:
            continue
        if s1 <= start:
            distance = start - s1
        elif s0 >= end:
            distance = s0 - end
        else:
            distance = 0
        if best is None or distance < best:
            best = distance
    return best


def _has_payment_anchor(fragment: str) -> bool:
    return _payment_anchor_distance(fragment, len(fragment), len(fragment)) is not None


def _duration_context_ok(sentence: str, start: int, end: int) -> bool:
    """期限只归属它所在的分句片段（中文「保修期为12个月，付款时扣留」
    里的付款动作不能把保修期认成账期）。

    - 片段内有付款锚点：与排除锚点（保修/逾期/违约…）按距离裁决，付款
      更近或没有排除锚点才接受——覆盖「收到发票后 30 天内支付」这类
      付款动词在数字之后的常见语序；
    - 片段内没有付款锚点：仅当上一分句有付款锚点且本片段没有排除锚点
      时接受（如「关于付款，期限为 30 天」）。
    """
    fragments = list(_iter_fragments(sentence))
    for index, (fragment, offset) in enumerate(fragments):
        fragment_end = offset + len(fragment)
        if not (start >= offset and end <= fragment_end):
            continue
        local_start, local_end = start - offset, end - offset
        payment = _payment_anchor_distance(fragment, local_start, local_end)
        excluded = _anchor_distance(fragment, local_start, local_end, NON_PAYMENT_CONTEXT)
        if payment is not None:
            if excluded is None:
                return True
            if payment < excluded:
                return True
            if payment == excluded:
                # 等距时区分语义：「到期后 30 天内付款」是付款截止期；
                # 「逾期 30 天支付」是违约条件，阈值语境等距时应拒绝。
                threshold = _anchor_distance(fragment, local_start, local_end, THRESHOLD_CONTEXT)
                return threshold is None or threshold > payment
            return False
        if excluded is not None:
            return False
        # 付款动作可以在相邻分句（中文常见：「…后 30 日内，向乙方支付…」）。
        previous_text = fragments[index - 1][0] if index > 0 else ""
        next_text = fragments[index + 1][0] if index + 1 < len(fragments) else ""
        return _has_payment_anchor(previous_text) or _has_payment_anchor(next_text)
    return False


def _is_date_fragment(text: str, start: int, unit_raw: str) -> bool:
    """排除日期片段：2025年3月、3月15日中的「3月」「15日」。"""
    before = text[:start].rstrip()
    if unit_raw == "月":
        return before.endswith("年")
    if unit_raw in ("日", "天"):
        return before.endswith("月") or before.endswith("年")
    return False


def _clause_number(raw: str) -> int | None:
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    return _cn_to_int(raw)


def _reference_numbers(text: str) -> list[int]:
    """引用短语里的全部条款号（含 第X条至第Y条 的区间展开）。"""
    numbers: list[int] = []
    for match in _CLAUSE_NO_RE.finditer(text):
        number = _clause_number(match.group(1))
        if number is not None:
            numbers.append(number)
    for match in _REF_RANGE_RE.finditer(text):
        start = _clause_number(match.group(1))
        end = _clause_number(match.group(2))
        if start is None or end is None or not 0 < end - start <= 20:
            continue
        numbers.extend(range(start, end + 1))
    return list(dict.fromkeys(numbers))


def _payment_reference_clauses(
    clauses: list[Clause], payment_clauses: list[Clause]
) -> tuple[list[Clause], bool]:
    """付款条款显式引用其它条款时，把被引用条款纳入付款期限抽取（只跟一层）。

    仅跟随付款语境同片段的引用；目标必须是本文件内的非例外条款，且未被
    付款关键词直接命中（后者本来就在抽取范围内）。同一短语的并列/范围目标
    全部跟随；引用了不存在的条款号时返回 unresolved=True，付款期限无法完整
    判定，调用方须降级 ambiguous（不得只报第一个目标的值）。
    """
    by_no: dict[int, Clause] = {}
    for clause in clauses:
        for number in clause.clause_no or []:
            by_no.setdefault(int(number), clause)
    known = {clause.chunk_id for clause in payment_clauses}
    referenced: list[Clause] = []
    unresolved = False
    for clause in payment_clauses:
        for match in _PAYMENT_REF_RE.finditer(clause.text):
            for target_no in _reference_numbers(match.group(0)):
                target = by_no.get(target_no)
                if target is None:
                    unresolved = True
                    continue
                if target.chunk_id in known or _is_exception_article(target):
                    continue
                known.add(target.chunk_id)
                referenced.append(target)
    return referenced, unresolved


# 被引用条款里，期限后若接动作谓语（完成验收/开具发票/提交对账单/赔偿…），
# 期限约束的是该动作，不是付款：引用路径必须拒绝（宁漏不猜）。
_REFERENCED_ACTION_VERBS = (
    "完成",
    "开具",
    "提交",
    "审批",
    "用印",
    "发起",
    "办理",
    "提出",
    "通知",
    "归档",
    "签署",
    "盖章",
    "赔偿",
    "整改",
    "维修",
    "安装",
    "调试",
    "培训",
    "出具",
    "申请",
    "催告",
    "履行",
    "确认",
    "对账",
    "验收",
    "开票",
    "交货",
    "交付",
    "供货",
    "发货",
)


def _referenced_duration_ok(sentence: str, start: int, end: int) -> bool:
    """被显式引用的条款没有付款锚点，引用本身提供付款语境。

    排除两类：保修/逾期等非付款语境片段；期限后接动作谓语（完成验收、
    开具发票、提交对账单…）——此时期限约束动作而非付款。
    """
    for fragment, offset in _iter_fragments(sentence):
        if start >= offset and end <= offset + len(fragment):
            local_start, local_end = start - offset, end - offset
            if _anchor_distance(fragment, local_start, local_end, NON_PAYMENT_CONTEXT) is not None:
                return False
            tail = fragment[local_end:]
            return not any(verb in tail for verb in _REFERENCED_ACTION_VERBS)
    return False


def extract_payment_term(clauses: list[Clause]) -> dict[str, Any]:
    # 标题为「例外」的独立条款归 exception 项：其顺延期限不参与付款基线判定。
    payment_clauses = [
        c
        for c in clauses
        if any(k in c.text for k in PAYMENT_KEYWORDS) and not _is_exception_article(c)
    ]
    referenced_clauses, unresolved_reference = _payment_reference_clauses(clauses, payment_clauses)
    referenced_ids = {clause.chunk_id for clause in referenced_clauses}

    def _context_ok(clause: Clause, sentence: str, start: int, end: int) -> bool:
        if _duration_context_ok(sentence, start, end):
            return True
        return clause.chunk_id in referenced_ids and _referenced_duration_ok(sentence, start, end)

    hits: list[dict[str, Any]] = []
    for clause in payment_clauses + referenced_clauses:
        for sentence in _iter_sentences(clause.text):
            for match in DURATION_RE.finditer(sentence):
                unit_raw = match.group(3)
                if not _context_ok(clause, sentence, match.start(), match.end()):
                    continue
                if _is_date_fragment(sentence, match.start(), unit_raw):
                    continue
                hits.append(
                    {
                        "value": int(match.group(1)),
                        "unit": UNIT_MAP.get(unit_raw),
                        "unit_raw": unit_raw,
                        "raw": match.group(0),
                        "_sentence": sentence,
                        "_start": match.start(),
                        "_end": match.end(),
                        "_clause": clause,
                    }
                )
            for match in MONTH_RE.finditer(sentence):
                if not _context_ok(clause, sentence, match.start(), match.end()):
                    continue
                if _is_date_fragment(sentence, match.start(), "月"):
                    continue
                if match.start() > 0 and sentence[match.start() - 1] in "第年月日同唯统数几千万":
                    continue
                if re.match(r"\s*\d+\s*[日号]", sentence[match.end() :]):
                    continue
                hits.append(
                    {
                        "value": int(match.group(1)),
                        "unit": "month",
                        "unit_raw": "月",
                        "raw": match.group(0),
                        "_sentence": sentence,
                        "_start": match.start(),
                        "_end": match.end(),
                        "_clause": clause,
                    }
                )
            for match in CN_MONTH_RE.finditer(sentence):
                value = _cn_to_int(match.group(1))
                if value is None:
                    continue
                if not _context_ok(clause, sentence, match.start(), match.end()):
                    continue
                if match.start() > 0 and sentence[match.start() - 1] in "第年月日同唯统数几千万":
                    continue
                # 「三月十五日」是日期，不是三个月期限。
                if re.match(
                    r"\s*(?:\d+|[一二三四五六七八九十]+)\s*[日号]", sentence[match.end() :]
                ):
                    continue
                hits.append(
                    {
                        "value": value,
                        "unit": "month",
                        "unit_raw": "月",
                        "raw": match.group(0),
                        "_sentence": sentence,
                        "_start": match.start(),
                        "_end": match.end(),
                        "_clause": clause,
                    }
                )
            for match in CN_DURATION_RE.finditer(sentence):
                value = _cn_to_int(match.group(1))
                unit_raw = match.group(3)
                if value is None:
                    continue
                if not _context_ok(clause, sentence, match.start(), match.end()):
                    continue
                if _is_date_fragment(sentence, match.start(), unit_raw):
                    continue
                # 同一日 / 唯一日期 / 统一日 / 数十日 / 一千二百 等复合词或
                # 约数里的数字不是期限，也不允许静默截半。
                if match.start() > 0 and sentence[match.start() - 1] in "第月年同唯统数几千万":
                    continue
                hits.append(
                    {
                        "value": value,
                        "unit": UNIT_MAP.get(unit_raw),
                        "unit_raw": unit_raw,
                        "raw": match.group(0),
                        "_sentence": sentence,
                        "_start": match.start(),
                        "_end": match.end(),
                        "_clause": clause,
                    }
                )
    hits = [
        hit
        for hit in hits
        if not _duration_owned_by_delivery(hit["_sentence"], hit["_start"], hit["_end"])
    ]
    if not hits:
        if unresolved_reference:
            # 引用了本文件不存在的条款：无法完整判定，不假装未识别。
            return {"status": "ambiguous", "reason": "reference_unresolved", "candidates": []}
        reason = "no_duration" if payment_clauses else "no_payment_clause"
        return {"status": "not_found", "reason": reason}
    ambiguous_units = [hit for hit in hits if hit["unit"] is None]
    distinct: list[dict[str, Any]] = []
    for hit in hits:
        # 单位已明确时按映射后单位去重（30天 / 30个自然日 是同一事实）；
        # 单位不明（「日」）保留原文单位参与去重，避免掩盖歧义。
        key = (
            (hit["value"], hit["unit"])
            if hit["unit"] is not None
            else (hit["value"], None, hit["unit_raw"])
        )
        if all(
            (
                (d["value"], d["unit"])
                if d["unit"] is not None
                else (d["value"], None, d["unit_raw"])
            )
            != key
            for d in distinct
        ):
            distinct.append(hit)
    matched_clauses = [hit["_clause"] for hit in hits]
    # 例外条款里的顺延期限归 exception 项：不参与付款基线（标题含例外词才排除，
    # 付款条款正文里出现「除外/另行约定」不会让整条付款文本失去主体/否定保护）。
    text_sha = _matched_text_sha_for(matched_clauses, "payment_term")
    clause_no_union = _clause_no_union(matched_clauses)
    if len(distinct) == 1 and not ambiguous_units and not unresolved_reference:
        hit = distinct[0]
        return {
            "status": "identified",
            "value": hit["value"],
            "unit": hit["unit"],
            "raw": hit["raw"],
            "text_sha256": text_sha,
            **_primary_locator(hit["_clause"]),
            "clause_no_union": clause_no_union,
        }
    reasons: list[str] = []
    if ambiguous_units:
        reasons.append("unit_unclear")
    if len(distinct) > 1:
        reasons.append("multiple_durations")
    if unresolved_reference:
        reasons.append("reference_unresolved")
    return {
        "status": "ambiguous",
        "reason": "+".join(reasons),
        "candidates": [
            {key: hit[key] for key in ("value", "unit", "unit_raw", "raw")} for hit in distinct
        ],
        "text_sha256": text_sha,
        **_primary_locator(distinct[0]["_clause"]),
        "clause_no_union": clause_no_union,
    }


def extract_start_condition(clauses: list[Clause]) -> dict[str, Any]:
    # 独立例外条款里的起算事件是顺延条件，不是付款起算条件本身。
    payment_clauses = [
        c
        for c in clauses
        if any(k in c.text for k in PAYMENT_KEYWORDS) and not _is_exception_article(c)
    ]
    events: list[dict[str, Any]] = []
    for clause in payment_clauses:
        for event in START_EVENTS:
            if event in clause.text and all(e["event"] != event for e in events):
                events.append({"event": event, "_clause": clause, **_primary_locator(clause)})
    if not events:
        reason = "no_start_event" if payment_clauses else "no_payment_clause"
        return {"status": "not_found", "reason": reason}
    first = events[0]
    locator = {
        key: first[key]
        for key in ("chunk_id", "chunk_index", "clause_no", "section_no", "page_number")
    }
    matched_clauses = [event["_clause"] for event in events]
    return {
        "status": "identified",
        "events": [event["event"] for event in events],
        "text_sha256": _matched_text_sha_for(matched_clauses, "start_condition"),
        **locator,
        "clause_no_union": _clause_no_union(matched_clauses),
    }


def _heading_contains(clause: Clause, terms: tuple[str, ...]) -> bool:
    """条款标题（必须有冒号分隔）是否命中给定术语；无冒号不判定为标题。"""
    parts = _HEADING_SPLIT_RE.split(clause.text, maxsplit=1)
    if len(parts) < 2:
        return False
    heading = parts[0].split("\n", 1)[0][:40]
    return any(term in heading for term in terms)


def _primary_term_hit(
    hits: list[dict[str, Any]],
    term_clauses: list[Clause],
    terms: tuple[str, ...],
) -> Clause:
    """静态主证据（无变化/无法判定时的回退）：标题命中优先，否则首个命中。

    变化项由 `refine_primary_locator` 在拿到两侧事实后指向实际变化条款；
    静态路径不再做概念重定向（未变化项指向标题条款才是用户期望的证据）。
    """
    heading_clauses = [clause for clause in term_clauses if _heading_contains(clause, terms)]
    return heading_clauses[0] if heading_clauses else hits[0]["_clause"]


def refine_primary_locator(
    item_key: str,
    base_facts: dict[str, Any],
    revised_facts: dict[str, Any],
    change_type: str,
    *,
    base_clauses: list[Clause],
    revised_clauses: list[Clause],
) -> None:
    """差异感知的主证据修正：该项确有变化时，把主定位指向“它的视角下文本
    实际变化”的那一条；无变化/无法判定时保留静态回退（标题/概念）结果。

    对验收/起算项，付款期限等噪音已被按项屏蔽，因此“付款天数变化”不会把
    主证据吸到付款条款；而“发票/对账新增”“验收期限变化”会把主证据移到真正
    变化的那一条。对付款项，起算/验收词元已屏蔽，主体/否定变化仍可检测。
    """
    if change_type not in ("modified", "added", "removed"):
        return
    side = "base" if change_type == "removed" else "revised"
    facts = base_facts if side == "base" else revised_facts
    side_clauses = base_clauses if side == "base" else revised_clauses
    other_clauses = revised_clauses if side == "base" else base_clauses

    other_counts: dict[str, int] = {}
    for clause in other_clauses:
        digest = _content_sha_for(clause, item_key)
        other_counts[digest] = other_counts.get(digest, 0) + 1
    candidates: list[Clause] = []
    for clause in side_clauses:
        digest = _content_sha_for(clause, item_key)
        if other_counts.get(digest, 0) <= 0:
            candidates.append(clause)
    if item_key != "exception":
        # 独立例外条款归 exception 项：不能劫持验收/付款/起算的证据。
        candidates = [c for c in candidates if not _is_exception_article(c)]
    if not candidates:
        return

    allowed = set(facts.get("clause_no_union") or facts.get("clause_no") or [])
    if not allowed:
        # section-numbered / 无编号文档没有 clause_no 可收敛；保留静态回退，
        # 绝不在整篇里按“第一个变化条款”乱移主证据。
        return
    scoped = [clause for clause in candidates if set(clause.clause_no) & allowed]
    static_chunk = facts.get("chunk_id")
    if static_chunk and any(clause.chunk_id == static_chunk for clause in scoped):
        # 静态主证据本身就在变化候选里：它已经是“变化的那一条”，不要被更早的
        # 其它变化条款（如新增的前部例外条款）劫持。
        return
    chosen = (scoped or candidates)[0]
    locator = _primary_locator(chosen)
    for key in ("chunk_id", "chunk_index", "clause_no", "section_no", "page_number"):
        facts[key] = locator[key]


def _collect_terms(
    clauses: list[Clause],
    terms: tuple[str, ...],
    item_key: str = "acceptance_invoice",
) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    for clause in clauses:
        # 例外条款的术语归 exception 项：它的「验收合格/发票」是顺延条件，
        # 不应成为验收/起算项的事实词元（否则新增例外会误报 modified）。
        if item_key != "exception" and _is_exception_article(clause):
            continue
        for term in _dedupe_terms(clause.text, terms):
            if all(hit["term"] != term for hit in hits):
                hits.append({"term": term, "_clause": clause, **_primary_locator(clause)})
    if not hits:
        return {"status": "not_found", "reason": "no_terms"}
    contributing_clauses = [hit["_clause"] for hit in hits]
    all_term_clauses = [
        clause
        for clause in clauses
        if any(term in clause.text for term in terms)
        and (item_key == "exception" or not _is_exception_article(clause))
    ]
    primary_clause = _primary_term_hit(hits, all_term_clauses, terms)
    # 验收项：同术语的第二条款也要参与变更检测（S1）；独立例外条款归例外项，
    # 不参与验收文本哈希（否则新增例外条款会误报验收 modified）。
    hash_clauses = contributing_clauses
    if item_key == "acceptance_invoice":
        hash_clauses = [c for c in all_term_clauses if not _is_exception_article(c)]
    return {
        "status": "identified",
        "terms": [hit["term"] for hit in hits],
        "text_sha256": _matched_text_sha_for(hash_clauses, item_key),
        **_primary_locator(primary_clause),
        "payload_clause_no": _clause_no_union(contributing_clauses),
        "clause_no_union": _clause_no_union(all_term_clauses),
    }


def extract_cross_refs(clauses: list[Clause]) -> list[str]:
    refs: list[str] = []
    for clause in clauses:
        for match in CROSS_REF_RE.finditer(clause.text):
            if match.group(0) not in refs:
                refs.append(match.group(0))
    return refs


def extract_acceptance_invoice(clauses: list[Clause]) -> dict[str, Any]:
    return _collect_terms(clauses, ACCEPTANCE_TERMS)


def extract_exception(clauses: list[Clause]) -> dict[str, Any]:
    facts = _collect_terms(clauses, EXCEPTION_TERMS, item_key="exception")
    refs = extract_cross_refs(clauses)
    facts["cross_refs"] = refs
    if facts["status"] == "identified" and refs:
        matched: dict[str, Clause] = {}
        for clause in clauses:
            if any(term in clause.text for term in facts.get("terms") or ()):
                matched[clause.chunk_id] = clause
            if any(ref in clause.text for ref in refs):
                matched[clause.chunk_id] = clause
        if matched:
            clauses_matched = list(matched.values())
            facts["text_sha256"] = _matched_text_sha(clauses_matched)
            # 交叉引用条款的“只改编号”也要能被 renumber 判定看到：payload 与
            # 展示 union 都纳入术语条款与交叉引用条款。
            ref_numbers = _clause_no_union(clauses_matched)
            facts["payload_clause_no"] = sorted(
                set(facts.get("payload_clause_no") or []) | set(ref_numbers)
            )
            facts["clause_no_union"] = sorted(
                set(facts.get("clause_no_union") or []) | set(ref_numbers)
            )
    return facts


EXTRACTORS = {
    "payment_term": extract_payment_term,
    "start_condition": extract_start_condition,
    "acceptance_invoice": extract_acceptance_invoice,
    "exception": extract_exception,
}


def _semantic_payload(item_key: str, facts: dict[str, Any]) -> Any:
    if facts["status"] != "identified":
        return None
    if item_key == "payment_term":
        return (facts.get("value"), facts.get("unit"))
    if item_key == "exception":
        # 例外项的事实包括命中的例外词与交叉引用；只看 terms 会把
        # 「详见第12条 → 详见第15条」这类实质变化报成无差异。
        return (
            tuple(sorted(facts.get("terms") or ())),
            tuple(sorted(facts.get("cross_refs") or ())),
        )
    # 集合语义：条款/事件的先后顺序不是事实差异（顺序变化由主条款文本哈希体现）。
    return tuple(sorted(facts.get("events") or facts.get("terms") or ()))


def align_item(
    item_key: str,
    base: dict[str, Any],
    revised: dict[str, Any],
    *,
    scan_truncated: bool = False,
) -> str:
    """只比较文本事实 + 归一化主条款文本；无法判定时不报「无差异」。"""
    base_found = base["status"] != "not_found"
    revised_found = revised["status"] != "not_found"
    result: str
    if not base_found and not revised_found:
        result = "unidentified"
    elif base["status"] == "ambiguous" and revised["status"] == "ambiguous":
        # 单位不明但数值可确定：候选集合相同 → 视为 payload 相同继续比较文本；
        # 候选集合不同 → modified；空候选 → unidentified（不猜）。
        def _candidates(facts: dict) -> set[tuple]:
            return {(c["value"], c["unit"], c["unit_raw"]) for c in (facts.get("candidates") or [])}

        base_candidates = _candidates(base)
        revised_candidates = _candidates(revised)
        if base_candidates and base_candidates == revised_candidates:
            base_sha = base.get("text_sha256")
            revised_sha = revised.get("text_sha256")
            if base_sha and revised_sha and base_sha != revised_sha:
                result = "modified"
            elif _locator_nos(base) != _locator_nos(revised):
                result = "renumbered"
            else:
                result = "unchanged"
        elif base_candidates and revised_candidates:
            result = "modified"
        else:
            result = "unidentified"
    elif base["status"] == "ambiguous" or revised["status"] == "ambiguous":
        # 一侧未识别时不能把另一侧断言成「新增/删除」。
        result = "unidentified"
    elif not base_found:
        result = "added"
    elif not revised_found:
        result = "removed"
    elif _semantic_payload(item_key, base) == _semantic_payload(item_key, revised):
        base_sha = base.get("text_sha256")
        revised_sha = revised.get("text_sha256")
        if base_sha and revised_sha and base_sha != revised_sha:
            # 事实词元相同但文本变了（否定/主体/限定语）：必须报差异。
            result = "modified"
        elif _locator_nos(base) != _locator_nos(revised):
            result = "renumbered"
        else:
            result = "unchanged"
    else:
        result = "modified"
    if scan_truncated and result != "modified":
        # 扫描被截断时不能宣称「无差异/仅重编号/新增/缺失」这类需要全量的结论。
        return "unidentified"
    return result


async def _load_clauses(
    session: AsyncSession, document: Document, side: str
) -> tuple[list[Clause], dict[str, Any]]:
    """读取一方的 parser 原文条款；未索引/无分块/混 legacy 都显式拒绝。"""
    if document.status != DocumentStatus.INDEXED:
        raise ConflictError(
            "案件文档当前不可用于分析（未索引或解析失败）",
            error_code="REVIEW_DOCUMENT_NOT_READY",
            detail={"side": side, "status": document.status.value},
        )
    total = int(
        (
            await session.execute(
                select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == document.id)
            )
        ).scalar()
        or 0
    )
    if total == 0:
        raise ConflictError(
            "案件文档没有可分析的分块",
            error_code="REVIEW_DOCUMENT_NOT_READY",
            detail={"side": side, "reason": "no_chunks"},
        )
    clauses: list[Clause] = []
    scanned = 0
    legacy = 0
    async for chunk in iter_chunks(session, document.id, max_chunks=MAX_EVIDENCE_SCAN_CHUNKS):
        scanned += 1
        raw = chunk_original_text(chunk)
        if raw is None:
            legacy += 1
            continue
        fields = extract_clause_fields(raw)
        clauses.append(
            Clause(
                chunk_id=chunk.id,
                chunk_index=chunk.chunk_index,
                text=normalize_clause_text(raw),
                raw_text=raw,
                clause_no=fields["clause_no"],
                section_no=fields["section_no"],
                page_number=chunk.page_number,
                char_start=chunk.start_pos,
                char_end=chunk.end_pos,
            )
        )
    if legacy > 0:
        # 混有未迁移分块 = 证据覆盖不完整；与 Batch 1 readiness 的 legacy_unverified 同口径。
        raise SourceProvenanceUnavailableError([document.id], unverified_count=legacy)
    return clauses, {
        "scanned_chunks": scanned,
        "truncated": scanned >= MAX_EVIDENCE_SCAN_CHUNKS and scanned < total,
    }


def _clause_by_locator(clauses: list[Clause], facts: dict[str, Any]) -> Clause | None:
    chunk_id = facts.get("chunk_id")
    if not chunk_id:
        return None
    for clause in clauses:
        if clause.chunk_id == chunk_id:
            return clause
    return None


async def _lock_case(session: AsyncSession, case: ReviewCase) -> ReviewCase:
    """与 review_export_service._lock_case 同口径：先结束只读事务再加锁。"""
    await session.commit()
    fresh = (
        await session.execute(select(ReviewCase).where(ReviewCase.id == case.id).with_for_update())
    ).scalar_one_or_none()
    if fresh is None:
        raise ConflictError("核验案件已被删除", error_code="REVIEW_CASE_NOT_FOUND")
    await session.refresh(fresh)
    return fresh


async def analyze_case(session: AsyncSession, case: ReviewCase) -> dict[str, Any]:
    """对 ready/in_review 案件做确定性抽取与对齐，更新 items 并快照主条款证据。"""
    case = await _lock_case(session, case)
    if case.archived_at is not None:
        raise ConflictError(
            "案件已归档：不能再执行核验分析",
            error_code="REVIEW_CASE_ARCHIVED",
            detail={"case_id": case.id, "status": case.status},
        )
    if case.status not in ("ready", "in_review"):
        raise ConflictError(
            "当前案件状态不允许执行核验分析",
            error_code="REVIEW_CASE_NOT_READY",
            detail={"status": case.status},
        )

    side_docs = {
        "policy": case.policy_document_id,
        "base": case.base_document_id,
        "revised": case.revised_document_id,
    }
    clauses: dict[str, list[Clause]] = {}
    scans: dict[str, dict[str, Any]] = {}
    metas = {}
    for side, document_id in side_docs.items():
        document = await session.get(Document, document_id)
        if document is None or document.organization_id != case.organization_id:
            raise ConflictError(
                "案件引用的文档已不存在，无法分析",
                error_code="REVIEW_DOCUMENT_UNAVAILABLE",
                detail={"document_id": document_id, "side": side},
            )
        clauses[side], scans[side] = await _load_clauses(session, document, side)
        metas[side] = await get_review_meta(session, document_id)
        if metas[side] is None:
            raise ConflictError(
                "案件文档缺少核验身份，无法分析",
                error_code="REVIEW_META_MISSING",
                detail={"document_id": document_id, "side": side},
            )

    items = (
        (
            await session.execute(
                select(ReviewItem)
                .where(ReviewItem.case_id == case.id)
                .order_by(ReviewItem.ordinal.asc(), ReviewItem.id.asc())
            )
        )
        .scalars()
        .all()
    )
    try:
        for item in items:
            extractor = EXTRACTORS.get(item.item_key)
            if extractor is None:
                continue
            facts_by_side = {side: extractor(clauses[side]) for side in side_docs}
            scan_truncated = scans["base"]["truncated"] or scans["revised"]["truncated"]
            change_type = align_item(
                item.item_key,
                facts_by_side["base"],
                facts_by_side["revised"],
                scan_truncated=scan_truncated,
            )
            # 差异感知主证据：有变化时指向该项视角下实际变化的条款；
            # 无变化/无法判定时保留 extractor 的静态回退（标题/概念）。
            refine_primary_locator(
                item.item_key,
                facts_by_side["base"],
                facts_by_side["revised"],
                change_type,
                base_clauses=clauses["base"],
                revised_clauses=clauses["revised"],
            )
            item.facts = {
                "extractor_version": EXTRACTOR_VERSION,
                "scan": {side: dict(scan) for side, scan in scans.items()},
                **facts_by_side,
            }
            item.change_type = change_type
            for side in side_docs:
                facts = facts_by_side[side]
                if facts["status"] == "not_found":
                    continue
                clause = _clause_by_locator(clauses[side], facts)
                if clause is None or metas[side] is None:
                    continue
                await ensure_candidate_evidence(
                    session,
                    case,
                    item,
                    side=side,
                    document_id=side_docs[side],
                    meta=metas[side],
                    chunk_id=clause.chunk_id,
                    chunk_index=clause.chunk_index,
                    quote=clause.raw_text,
                    page_number=clause.page_number,
                    char_start=clause.char_start,
                    char_end=clause.char_end,
                )
        await session.commit()
    except IntegrityError as exc:
        # 并发两次 analyze 同时补同一 (item, side, chunk)：唯一约束最后兜底。
        # IntegrityError 可能在任何后续 SELECT 的 autoflush 阶段抛出，因此
        # try 覆盖整个写入循环 + commit，而不是只包住 commit。
        await session.rollback()
        raise ConflictError(
            "核验分析并发冲突，请重试", error_code="REVIEW_CASE_ANALYSIS_CONFLICT"
        ) from exc
    # commit 后 onupdate 列（updated_at）可能处于过期状态；在**当前会话**里重载
    # 案件再组装详情，避免对游离对象 refresh 或同步属性访问触发隐式 IO。
    fresh = (
        await session.execute(select(ReviewCase).where(ReviewCase.id == case.id).with_for_update())
    ).scalar_one_or_none()
    if fresh is None:
        raise ConflictError("核验案件已被删除", error_code="REVIEW_CASE_NOT_FOUND")
    await session.refresh(fresh)
    return await case_detail(session, fresh)
