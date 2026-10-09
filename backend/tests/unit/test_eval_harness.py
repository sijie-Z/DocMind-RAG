"""#208 Phase 1 评估 harness 的判别用例（无 ES / 无网络，秒级）。

覆盖四类：
1. 条号解析与归一化（中文数字/阿拉伯数字/§ 形式/边界与拒绝规则）；
2. golden 加载器的严格校验（每种数据错误都必须被抓住并带定位）；
3. 指标判定（命中/未命中/负难例/错误态）与聚合口径；
4. 真实 golden set v1 的自洽与 provenance（gold 必须能在固定语料中定义位证明）。
"""

import json
import os
from pathlib import Path

import pytest

from evals.chunking import split_corpus_file
from evals.clauses import (
    clause_definitions,
    clause_mentions,
    normalize_clause_ref,
    parse_cn_number,
)
from evals.golden import GoldenItem, GoldenSet, GoldenSetError, Locator, NearMiss, load_golden_set
from evals.harness import RetrievalObservation, run_eval
from evals.metrics import aggregate, evaluate_item, wilson_ci
from evals.report import build_report, render_markdown, write_json, write_markdown
from evals.runner import _apply_env, build_parser

_BACKEND = Path(__file__).resolve().parents[2]
_GOLDEN_V1 = _BACKEND / "evals" / "golden_set" / "clause_set_v1.json"


# ---------------------------------------------------------------------------
# 1. 条号解析
# ---------------------------------------------------------------------------


class TestClauseParsing:
    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ("第七条", "第7条"),
            ("第7条", "第7条"),
            ("第 7 条", "第7条"),
            ("第十七条", "第17条"),
            ("第二十三条", "第23条"),
            ("第一百零五条", "第105条"),
            ("§2.3", "§2.3"),
            ("2.3", "§2.3"),
            ("2.10", "§2.10"),
            ("7", None),  # 裸整数有歧义 → 明确拒绝
            ("第7.5条", None),  # 「第…」开头但非条号格式
            ("第七条第二款", None),
            ("", None),
            ("abc", None),
        ],
    )
    def test_normalize_clause_ref(self, raw, want):
        assert normalize_clause_ref(raw) == want

    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ("七", 7),
            ("十", 10),
            ("十七", 17),
            ("二十", 20),
            ("一百零五", 105),
            ("一千二百三十四", 1234),
            ("12", 12),
            ("abc", None),
            ("一又二", None),
        ],
    )
    def test_parse_cn_number(self, raw, want):
        assert parse_cn_number(raw) == want

    def test_definitions_require_line_start(self):
        # 定义位：行首条号算命中
        assert clause_definitions("第3条 知识库中的文档数据……") == {"第3条"}
        # 正文引用（行中）不算命中 —— 防止「引用他条号的 chunk」冒充命中
        assert clause_definitions("本条与第 7 条所列规则冲突的申请一律不予受理。") == set()
        assert clause_definitions("依第9条的要求，每季度复核一次。") == set()

    def test_definitions_section_heading(self):
        assert clause_definitions("## 2.3 告警响应时限\n\nP1 ……") == {"§2.3"}
        assert clause_definitions("2.3 告警响应时限") == {"§2.3"}
        # 行中的小数（测量值）不构成章节定义
        assert clause_definitions("错误率连续 5 分钟超过 10% 即回滚。") == set()
        assert clause_definitions("版本 2.1 发布说明") == set()

    def test_definitions_heading_prefixed_clause(self):
        assert clause_definitions("# 第7条 组织级隔离") == {"第7条"}

    def test_definitions_multiple_ids_expand(self):
        text = "第8条 正文\n第9条 正文"
        assert clause_definitions(text) == {"第8条", "第9条"}

    def test_mentions_include_references(self):
        text = "第8条 权限变更……与第 7 条所列组织隔离规则冲突的申请不予受理。"
        assert clause_definitions(text) == {"第8条"}
        assert clause_mentions(text) == {"第8条", "第7条"}


# ---------------------------------------------------------------------------
# 2. golden 加载器
# ---------------------------------------------------------------------------


def _minimal_dataset() -> dict:
    return {
        "dataset_id": "unit_ds",
        "version": 1,
        "corpus_dir": ".",
        "corpus_files": ["corpus.md"],
        "top_k": 5,
        "items": [
            {
                "id": "a",
                "type": "clause_lookup",
                "query": "第七条是什么？",
                "expect": "hit",
                "gold": [{"file": "corpus.md", "clause": "第七条"}],
            }
        ],
    }


def _write_ds(tmp_path: Path, data: dict) -> Path:
    (tmp_path / "corpus.md").write_text("第7条 正文\n", encoding="utf-8")
    p = tmp_path / "ds.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


class TestGoldenLoader:
    def test_loads_and_normalizes(self, tmp_path):
        gs = load_golden_set(_write_ds(tmp_path, _minimal_dataset()))
        assert gs.items[0].gold[0].clause == "第7条"  # 「第七条」归一化
        assert gs.items[0].expect == "hit"

    def test_hit_without_gold_rejected(self, tmp_path):
        data = _minimal_dataset()
        data["items"][0]["gold"] = []
        with pytest.raises(GoldenSetError, match="必须提供至少一条 gold"):
            load_golden_set(_write_ds(tmp_path, data))

    def test_empty_with_gold_rejected(self, tmp_path):
        data = _minimal_dataset()
        data["items"][0]["expect"] = "empty"
        with pytest.raises(GoldenSetError, match="不允许提供 gold"):
            load_golden_set(_write_ds(tmp_path, data))

    def test_bad_clause_ref_rejected(self, tmp_path):
        data = _minimal_dataset()
        data["items"][0]["gold"][0]["clause"] = "7"
        with pytest.raises(GoldenSetError, match="无法解析"):
            load_golden_set(_write_ds(tmp_path, data))

    def test_unknown_file_rejected(self, tmp_path):
        data = _minimal_dataset()
        data["items"][0]["gold"][0]["file"] = "nope.md"
        with pytest.raises(GoldenSetError, match="不在 corpus_files"):
            load_golden_set(_write_ds(tmp_path, data))

    def test_duplicate_id_rejected(self, tmp_path):
        data = _minimal_dataset()
        data["items"].append(dict(data["items"][0]))
        with pytest.raises(GoldenSetError, match="重复"):
            load_golden_set(_write_ds(tmp_path, data))

    def test_missing_corpus_file_rejected(self, tmp_path):
        data = _minimal_dataset()
        data["corpus_files"] = ["ghost.md"]
        with pytest.raises(GoldenSetError, match="语料文件缺失"):
            load_golden_set(_write_ds(tmp_path, data))


# ---------------------------------------------------------------------------
# 3. 指标
# ---------------------------------------------------------------------------


def _item(item_id="x", expect="hit", gold=(("f.md", "第7条"),), near=()) -> GoldenItem:
    return GoldenItem(
        id=item_id,
        type="clause_lookup",
        query="q",
        expect=expect,
        gold=tuple(Locator(file=f, clause=c) for f, c in gold),
        near_miss=tuple(NearMiss(file=f, clause=c, why="w") for f, c in near),
    )


def _hit_result(filename: str, clause: str) -> dict:
    return {"filename": filename, "content": f"{clause} 正文内容", "score": 0.9}


class TestMetrics:
    def test_hit_at_rank1(self):
        o = evaluate_item(_item(), [_hit_result("f.md", "第7条")])
        assert (o.passed, o.gold_rank) == (True, 1)

    def test_hit_at_rank2(self):
        o = evaluate_item(
            _item(), [{"filename": "other.md", "content": "别的内容"}, _hit_result("f.md", "第7条")]
        )
        assert (o.passed, o.gold_rank) == (True, 2)

    def test_same_clause_wrong_file_is_miss(self):
        o = evaluate_item(_item(), [_hit_result("wrong.md", "第7条")])
        assert (o.passed, o.gold_rank) == (False, None)

    def test_mentioned_but_not_defined_is_miss(self):
        o = evaluate_item(
            _item(),
            [{"filename": "f.md", "content": "与第 7 条冲突的申请不予受理。", "score": 0.9}],
        )
        assert (o.passed, o.gold_rank) == (False, None)

    def test_near_miss_recorded_even_when_gold_hit(self):
        o = evaluate_item(
            _item(near=(("f.md", "第8条"),)),
            [_hit_result("f.md", "第8条"), _hit_result("f.md", "第7条")],
        )
        assert o.passed is True and o.gold_rank == 2
        assert o.near_miss_rank == 1 and o.matched_near_miss == ["f.md:第8条"]

    def test_negative_requires_empty(self):
        assert evaluate_item(_item(expect="empty", gold=()), []).passed is True
        assert (
            evaluate_item(_item(expect="empty", gold=()), [_hit_result("f.md", "第3条")]).passed
            is False
        )

    def test_error_not_counted_as_miss(self):
        o = evaluate_item(_item(), [], status="error", error="RuntimeError: x")
        assert o.passed is False and o.status == "error"

    def test_wilson_bounds(self):
        lo, hi = wilson_ci(0, 10)
        assert lo == 0.0 and 0.0 < hi < 0.35
        lo, hi = wilson_ci(10, 10)
        assert hi == 1.0 and 0.6 < lo < 1.0
        assert wilson_ci(0, 0) is None

    def test_aggregate_rates_and_error_isolation(self):
        outcomes = [
            evaluate_item(_item("p1", near=(("f.md", "第8条"),)), [_hit_result("f.md", "第7条")]),
            evaluate_item(_item("p2", near=(("f.md", "第8条"),)), [_hit_result("f.md", "第8条")]),
            evaluate_item(
                _item("p3", near=(("f.md", "第8条"),)),
                [{"filename": "f.md", "content": "无关"}, _hit_result("f.md", "第7条")],
            ),
            evaluate_item(_item("n1", expect="empty", gold=()), []),
            evaluate_item(_item("n2", expect="empty", gold=()), [_hit_result("f.md", "第3条")]),
            evaluate_item(_item("e1"), [], status="error", error="boom"),
        ]
        agg = aggregate(outcomes, cutoffs=(1, 3, 5))
        assert agg["counts"] == {
            "total": 6,
            "evaluated": 5,
            "errors": 1,
            "positives": 3,
            "negatives": 2,
            "positives_with_near_miss": 3,
        }
        assert agg["clause_hit@1"] == pytest.approx(1 / 3)
        assert agg["clause_hit@3"] == pytest.approx(2 / 3)
        assert agg["clause_hit@5"] == pytest.approx(2 / 3)
        assert agg["near_miss@5"] == pytest.approx(1 / 3)  # p2 误召（gold 未中）
        assert agg["mrr"] == pytest.approx((1.0 + 0.0 + 0.5) / 3)
        assert agg["empty_accuracy"] == pytest.approx(0.5)
        assert agg["error_items"] == ["e1"]


# ---------------------------------------------------------------------------
# 4. harness + 报告
# ---------------------------------------------------------------------------


class _FakeRetriever:
    def __init__(self, mapping: dict[str, list[dict]]):
        self.mapping = mapping
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, query: str, top_k: int) -> RetrievalObservation:
        self.calls.append((query, top_k))
        if query == "raise":
            raise RuntimeError("backend down")
        return RetrievalObservation(results=self.mapping.get(query, []), strategy="hybrid")


class TestHarnessAndReport:
    @pytest.mark.asyncio
    async def test_run_eval_end_to_end_and_error_capture(self):
        items = [
            _item("a", gold=(("f.md", "第7条"),), near=(("f.md", "第8条"),)),
            _item("b", gold=(("f.md", "第9条"),)),
            GoldenItem(id="neg", type="unanswerable", query="raise", expect="empty", gold=()),
        ]
        fake = _FakeRetriever(
            {"q": [_hit_result("f.md", "第8条")]}  # a/b 都不中；a 误召 near-miss
        )
        runs = await run_eval(items, fake, top_k=5)
        assert [r.outcome.status for r in runs] == ["ok", "ok", "error"]
        assert runs[0].outcome.passed is False and runs[0].outcome.near_miss_rank == 1
        assert runs[2].outcome.error and "backend down" in runs[2].outcome.error

    @pytest.mark.asyncio
    async def test_report_json_and_markdown(self, tmp_path):
        item = _item("a", gold=(("f.md", "第7条"),), near=(("f.md", "第8条"),))
        fake = _FakeRetriever({"q": [_hit_result("f.md", "第8条"), _hit_result("f.md", "第7条")]})
        runs = await run_eval((item,), fake, top_k=5)
        gs = GoldenSet(
            dataset_id="ds",
            version=1,
            description="",
            path=tmp_path / "x.json",
            corpus_dir=tmp_path,
            corpus_files=("f.md",),
            top_k=5,
            items=(item,),
        )
        report = build_report(
            gs,
            runs,
            index="idx",
            org_id=9901,
            top_k=5,
            retriever_label="fake",
            generated_at="2026-10-09T00:00:00+00:00",
        )
        json.dumps(report, ensure_ascii=False)  # 必须可序列化
        assert report["aggregates"]["clause_hit@5"] == 1.0
        assert report["items"][0]["results"][0]["defs"] == ["第8条"]

        md = render_markdown(report)
        assert "ClauseHit@5" in md and "`a`" in md and "PASS" in md
        jp = write_json(report, tmp_path / "out" / "r.json")
        mp = write_markdown(report, tmp_path / "out" / "r.md")
        assert json.loads(jp.read_text(encoding="utf-8"))["meta"]["index"] == "idx"
        assert "NearMiss" in mp.read_text(encoding="utf-8")


class TestRunnerEnv:
    def test_apply_env_sets_index_and_hosts(self, monkeypatch):
        monkeypatch.setenv("ELASTICSEARCH_INDEX_NAME", "orig_idx")
        monkeypatch.setenv("ELASTICSEARCH_HOSTS", '["http://orig:9200"]')
        args = build_parser().parse_args(
            ["check", "--index", "eval_idx", "--es-hosts", "a:9200,b:9200"]
        )
        _apply_env(args)
        assert os.environ["ELASTICSEARCH_INDEX_NAME"] == "eval_idx"
        assert json.loads(os.environ["ELASTICSEARCH_HOSTS"]) == ["a:9200", "b:9200"]

    def test_apply_env_passthrough_json_hosts(self, monkeypatch):
        monkeypatch.setenv("ELASTICSEARCH_HOSTS", "[]")
        args = build_parser().parse_args(["run", "--es-hosts", '["http://x:9200"]'])
        _apply_env(args)
        assert json.loads(os.environ["ELASTICSEARCH_HOSTS"]) == ["http://x:9200"]


# ---------------------------------------------------------------------------
# 5. 真实 golden set v1 的自洽与 provenance
# ---------------------------------------------------------------------------


class TestGoldenSetV1:
    @pytest.fixture(scope="class")
    def gs(self):
        return load_golden_set(_GOLDEN_V1)

    def test_scale_and_shape(self, gs):
        assert 20 <= len(gs.items) <= 30
        positives = gs.positive_items()
        negatives = gs.negative_items()
        assert len(positives) >= 20
        assert len(negatives) >= 3
        assert sum(1 for it in positives if it.near_miss) >= 20  # near-miss 起步要求
        # 数字归一化对照对（#2.10 基线证据）必须存在：同一 gold、两种数字写法
        ids = {it.id for it in gs.items}
        assert {"clause_chinese_7", "clause_arabic_7"} <= ids
        assert {"clause_chinese_12", "clause_12_deletion"} <= ids

    def test_every_corpus_file_is_referenced(self, gs):
        referenced = set()
        for it in gs.items:
            referenced.update(loc.file for loc in it.gold)
            referenced.update(nm.file for nm in it.near_miss)
        assert referenced == set(gs.corpus_files)

    def test_provenance_gold_definable_in_exactly_one_chunk(self, gs):
        """每个 gold/near-miss 条号必须在语料中**恰好一个 chunk 的定义位**上可证。

        这是「golden set 自洽」的核心：不许出现语料里根本没有的条号，
        也不许一个条号泄漏到多个 chunk（正文引用行首化 = fixture 事故）。
        """
        cache: dict[str, list[tuple[str, set[str]]]] = {}
        for fname in gs.corpus_files:
            text = (gs.corpus_dir / fname).read_text(encoding="utf-8")
            chunks = split_corpus_file(text)
            cache[fname] = [(c, clause_definitions(c)) for c in chunks]

        for it in gs.items:
            for loc in list(it.gold) + list(it.near_miss):
                chunks = cache[loc.file]
                defining = [c for c, defs in chunks if loc.clause in defs]
                assert len(defining) == 1, (
                    f"{it.id}: {loc.label()} 在 {loc.file} 中定义位出现 {len(defining)} 次"
                    f"（应为 1）"
                )

    def test_chunks_are_one_fact_each(self, gs):
        for fname in gs.corpus_files:
            text = (gs.corpus_dir / fname).read_text(encoding="utf-8")
            chunks = split_corpus_file(text)
            def_sets = [clause_definitions(c) for c in chunks]
            assert all(len(d) <= 1 for d in def_sets), f"{fname} 存在多定义位 chunk"
            empty = sum(1 for d in def_sets if not d)
            assert empty <= 1, f"{fname} 有无定义位的非前言 chunk（{empty} 个）"

    def test_item_fields_sane(self, gs):
        for it in gs.items:
            assert it.query.strip()
            assert isinstance(it.critical, bool)
            assert it.type != "unanswerable" or it.expect == "empty"
