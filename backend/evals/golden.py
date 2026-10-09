"""golden set 的加载与严格校验（纯标准库）。

数据格式（JSON，见 golden_set/clause_set_v1.json）：

```jsonc
{
  "dataset_id": "docmind_clause_v1",
  "version": 1,
  "description": "...",
  "corpus_dir": "corpus",            // 相对本文件的目录
  "corpus_files": ["01-....md", ...],
  "top_k": 5,
  "items": [
    {
      "id": "clause_arabic_3",       // 全局唯一
      "type": "clause_lookup",       // clause_lookup|semantic|numerical|cross_doc|conflict|unanswerable
      "query": "…",
      "expect": "hit",               // hit（必须命中 gold）| empty（必须返回空）
      "gold": [{"file": "01-….md", "clause": "第3条"}],
      "near_miss": [{"file": "01-….md", "clause": "第4条", "why": "…"}],
      "critical": false,
      "notes": "…"
    }
  ]
}
```

校验纪律（宁严勿松——golden set 的每种错误都会污染分数）：
- id 唯一；query 非空；expect ∈ {hit, empty}；
- expect=hit 必须给 gold；expect=empty 不允许给 gold（负例没有正确答案）；
- file 必须在 corpus_files 里且对应文件存在；
- clause 必须能被 ``normalize_clause_ref`` 归一化（「第七条」「第 7 条」「2.3」
  均可，裸整数 `7` 拒绝）；加载后统一存规范形式；
- near_miss 的 file/clause 校验同上，why 可为空。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .clauses import normalize_clause_ref

VALID_EXPECT = ("hit", "empty")
VALID_TYPES = (
    "clause_lookup",
    "semantic",
    "numerical",
    "cross_doc",
    "conflict",
    "unanswerable",
)


class GoldenSetError(ValueError):
    """golden set 格式/内容错误（带定位信息，便于人直接修）。"""


@dataclass(frozen=True)
class Locator:
    """gold / near-miss 的定位：某文件中的某个条号。"""

    file: str
    clause: str  # 规范形式：第N条 / §X.Y

    def label(self) -> str:
        return f"{self.file}:{self.clause}"


@dataclass(frozen=True)
class NearMiss:
    file: str
    clause: str
    why: str = ""

    def label(self) -> str:
        return f"{self.file}:{self.clause}"


@dataclass(frozen=True)
class GoldenItem:
    id: str
    type: str
    query: str
    expect: str
    gold: tuple[Locator, ...]
    near_miss: tuple[NearMiss, ...] = ()
    critical: bool = False
    notes: str = ""


@dataclass(frozen=True)
class GoldenSet:
    dataset_id: str
    version: int
    description: str
    path: Path
    corpus_dir: Path
    corpus_files: tuple[str, ...]
    top_k: int
    items: tuple[GoldenItem, ...] = ()

    def positive_items(self) -> list[GoldenItem]:
        return [it for it in self.items if it.expect == "hit"]

    def negative_items(self) -> list[GoldenItem]:
        return [it for it in self.items if it.expect == "empty"]


def _expect_str(obj: object, where: str) -> str:
    if not isinstance(obj, str) or not obj.strip():
        raise GoldenSetError(f"{where}: 必须是非空字符串，得到 {obj!r}")
    return obj.strip()


def _parse_locator(raw: object, where: str, corpus_files: tuple[str, ...]) -> Locator:
    if not isinstance(raw, dict):
        raise GoldenSetError(f"{where}: gold/near_miss 条目必须是对象，得到 {raw!r}")
    file = _expect_str(raw.get("file"), f"{where}.file")
    if file not in corpus_files:
        raise GoldenSetError(
            f"{where}.file = {file!r} 不在 corpus_files 里（{list(corpus_files)}）"
        )
    clause_raw = raw.get("clause")
    clause = normalize_clause_ref(clause_raw) if isinstance(clause_raw, str) else None
    if clause is None:
        raise GoldenSetError(
            f"{where}.clause = {clause_raw!r} 无法解析；"
            f"请写「第七条」/「第7条」/「§2.3」/「2.3」（裸整数有歧义，不接受）"
        )
    return Locator(file=file, clause=clause)


def _parse_item(raw: object, idx: int, corpus_files: tuple[str, ...]) -> GoldenItem:
    where = f"items[{idx}]"
    if not isinstance(raw, dict):
        raise GoldenSetError(f"{where}: 必须是对象")
    item_id = _expect_str(raw.get("id"), f"{where}.id")
    where = f"items[{idx}] ({item_id})"
    item_type = _expect_str(raw.get("type"), f"{where}.type")
    if item_type not in VALID_TYPES:
        raise GoldenSetError(f"{where}.type = {item_type!r} 不在 {VALID_TYPES}")
    query = _expect_str(raw.get("query"), f"{where}.query")
    expect = _expect_str(raw.get("expect"), f"{where}.expect")
    if expect not in VALID_EXPECT:
        raise GoldenSetError(f"{where}.expect = {expect!r} 必须是 {VALID_EXPECT} 之一")

    gold_raw = raw.get("gold") or []
    near_raw = raw.get("near_miss") or []
    if not isinstance(gold_raw, list) or not isinstance(near_raw, list):
        raise GoldenSetError(f"{where}: gold / near_miss 必须是列表")
    gold = tuple(
        _parse_locator(g, f"{where}.gold[{i}]", corpus_files) for i, g in enumerate(gold_raw)
    )
    near_miss_list: list[NearMiss] = []
    for i, n in enumerate(near_raw):
        loc = _parse_locator(n, f"{where}.near_miss[{i}]", corpus_files)
        why = str(n.get("why") or "").strip() if isinstance(n, dict) else ""
        near_miss_list.append(NearMiss(file=loc.file, clause=loc.clause, why=why))
    near_miss = tuple(near_miss_list)
    if expect == "hit" and not gold:
        raise GoldenSetError(f"{where}: expect=hit 必须提供至少一条 gold")
    if expect == "empty" and gold:
        raise GoldenSetError(f"{where}: expect=empty 不允许提供 gold（负例没有正确答案）")
    return GoldenItem(
        id=item_id,
        type=item_type,
        query=query,
        expect=expect,
        gold=gold,
        near_miss=near_miss,
        critical=bool(raw.get("critical", False)),
        notes=str(raw.get("notes") or ""),
    )


def load_golden_set(path: str | Path) -> GoldenSet:
    """加载并严格校验 golden set；任何问题抛 GoldenSetError（带定位）。"""
    p = Path(path).resolve()
    if not p.is_file():
        raise GoldenSetError(f"golden set 文件不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise GoldenSetError(f"{p} 不是合法 JSON: {e}") from e
    if not isinstance(data, dict):
        raise GoldenSetError(f"{p}: 顶层必须是对象")

    dataset_id = _expect_str(data.get("dataset_id"), "dataset_id")
    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        raise GoldenSetError(f"version 必须是 ≥1 的整数，得到 {version!r}")
    corpus_files_raw = data.get("corpus_files")
    if not isinstance(corpus_files_raw, list) or not corpus_files_raw:
        raise GoldenSetError("corpus_files 必须是非空列表")
    corpus_files = tuple(_expect_str(f, "corpus_files[]") for f in corpus_files_raw)
    if len(set(corpus_files)) != len(corpus_files):
        raise GoldenSetError("corpus_files 有重复项")
    top_k = data.get("top_k", 5)
    if not isinstance(top_k, int) or top_k < 1:
        raise GoldenSetError(f"top_k 必须是 ≥1 的整数，得到 {top_k!r}")

    corpus_dir = (p.parent / str(data.get("corpus_dir") or "corpus")).resolve()
    for fname in corpus_files:
        if not (corpus_dir / fname).is_file():
            raise GoldenSetError(f"语料文件缺失: {corpus_dir / fname}")

    items_raw = data.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        raise GoldenSetError("items 必须是非空列表")
    items = tuple(_parse_item(raw, i, corpus_files) for i, raw in enumerate(items_raw))
    seen: set[str] = set()
    dups: list[str] = []
    for it in items:
        if it.id in seen:
            dups.append(it.id)
        seen.add(it.id)
    if dups:
        raise GoldenSetError(f"item id 重复: {sorted(dups)}")

    return GoldenSet(
        dataset_id=dataset_id,
        version=version,
        description=str(data.get("description") or ""),
        path=p,
        corpus_dir=corpus_dir,
        corpus_files=corpus_files,
        top_k=top_k,
        items=items,
    )
