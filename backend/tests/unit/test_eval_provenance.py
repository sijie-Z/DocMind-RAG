"""Batch 15：eval 原文口径最小闭环（O 优先 / text_basis / seed 契约 / dense 透传）。

产品契约：条款判定依据必须是可验证原文 O（text_provenance=parser_original）；
缺失 O 时才回退 E（compat），并如实记录 text_basis。fixture 语料 E==O，
但字段契约必须与产品一致，否则未来 E≠O 时评测会静默测错正文。
"""

import json
from types import SimpleNamespace

import pytest

from evals.golden import GoldenItem, GoldenSet, Locator
from evals.harness import ItemRun
from evals.metrics import aggregate, evaluate_item, result_text
from evals.report import build_report
from evals.runner import PROTOCOL_KEYS, _dense_retrieve, _seed_source


def _item(item_id="x", expect="hit", gold=(("f.md", "第7条"),)) -> GoldenItem:
    return GoldenItem(
        id=item_id,
        type="clause_lookup",
        query="q",
        expect=expect,
        gold=tuple(Locator(file=f, clause=c) for f, c in gold),
    )


def _o_result(filename: str, clause: str) -> dict:
    return {
        "filename": filename,
        "content": f"{clause} 正文内容",
        "original_text": f"{clause} 正文内容",
        "text_provenance": "parser_original",
        "score": 0.9,
    }


class TestResultText:
    def test_prefers_verified_original(self):
        result = {
            "content": "【文档背景：摘要】\n第7条 压缩后的正文",
            "original_text": "第7条 付款期限为30天。",
            "text_provenance": "parser_original",
        }
        assert result_text(result) == ("第7条 付款期限为30天。", "original")

    def test_missing_or_unverified_provenance_falls_back_to_e(self):
        result = {
            "content": "E 第7条",
            "original_text": "第7条",
            "text_provenance": "legacy_unverified",
        }
        assert result_text(result) == ("E 第7条", "compat")
        # 有 original_text 但没标 provenance：同样不可信
        assert result_text({"content": "E", "original_text": "O"}) == ("E", "compat")

    def test_prompt_text_preferred_over_content_in_compat(self):
        result = {"content": "content", "prompt_text": "prompt", "text_provenance": "legacy"}
        assert result_text(result) == ("prompt", "compat")

    def test_whitespace_original_falls_back(self):
        result = {
            "content": "E 第7条",
            "original_text": "   ",
            "text_provenance": "parser_original",
        }
        assert result_text(result) == ("E 第7条", "compat")

    def test_empty_result(self):
        assert result_text({}) == ("", "compat")


class TestEvaluateUsesOriginal:
    def test_gold_matched_from_original_not_enhanced_content(self):
        # E 是压缩/改写后的文本，已不含条款定义行；O 才是合同原文。
        result = {
            "filename": "f.md",
            "content": "（摘要）付款为 30 天",
            "original_text": "第7条 付款期限为30天。",
            "text_provenance": "parser_original",
        }
        outcome = evaluate_item(_item(), [result])
        assert outcome.passed is True and outcome.gold_rank == 1
        assert outcome.text_basis == "original"
        assert outcome.top1_defs == ["第7条"]

    def test_legacy_content_still_matches_and_marks_compat(self):
        outcome = evaluate_item(
            _item(), [{"filename": "f.md", "content": "第7条 正文", "score": 0.9}]
        )
        assert outcome.passed is True
        assert outcome.text_basis == "compat"

    def test_mixed_basis_when_results_differ(self):
        results = [_o_result("f.md", "第7条"), {"filename": "g.md", "content": "无关"}]
        outcome = evaluate_item(_item(), results)
        assert outcome.text_basis == "mixed"

    def test_empty_negative_has_no_basis(self):
        outcome = evaluate_item(_item("neg", expect="empty", gold=()), [])
        assert outcome.text_basis == "none"


class TestAggregateBasisCounts:
    def test_counts_only_items_with_results(self):
        outcomes = [
            evaluate_item(_item("a"), [_o_result("f.md", "第7条")]),
            evaluate_item(_item("b"), [{"filename": "f.md", "content": "第7条 正文"}]),
            evaluate_item(_item("neg", expect="empty", gold=()), []),
            evaluate_item(_item("err"), [], status="error", error="boom"),
        ]
        agg = aggregate(outcomes)
        # 空结果的负例与错误项没有「判定依据」，不参与分布
        assert agg["counts"]["text_basis"] == {"original": 1, "compat": 1}


class TestSeedSourceContract:
    def test_contract_fields(self):
        source = _seed_source(
            fname="f.md",
            chunk="第7条 正文",
            vector=[0.1, 0.2],
            organization_id="9901",
            document_id="eval-f",
            chunk_index=3,
        )
        assert source["content"] == source["chunk_text"] == "第7条 正文"
        assert source["original_text"] == "第7条 正文"
        assert source["text_provenance"] == "parser_original"
        assert source["organization_id"] == "9901"
        assert source["document_id"] == "eval-f"
        assert source["metadata"]["chunk_index"] == 3
        json.dumps(source)  # 必须可序列化


class TestDenseProvenancePassthrough:
    @pytest.mark.asyncio
    async def test_dense_results_carry_original_and_provenance(self, monkeypatch):
        from app.core.elasticsearch import ElasticsearchTools

        captured_queries: list[dict] = []

        async def _fake_search(_query):
            captured_queries.append(_query)
            return {
                "hits": {
                    "hits": [
                        {
                            "_score": 1.5,
                            "_source": {
                                "content": "E 第7条",
                                "chunk_text": "E 第7条",
                                "original_text": "第7条 原文",
                                "text_provenance": "parser_original",
                                "filename": "f.md",
                                "document_id": "d1",
                                "chunk_index": 0,
                            },
                        },
                        {
                            "_score": 1.4,
                            "_source": {
                                "content": "legacy 内容",
                                "filename": "f.md",
                                "document_id": "d2",
                            },
                        },
                    ]
                }
            }

        monkeypatch.setattr(ElasticsearchTools, "search_documents", _fake_search)

        class _Embed:
            async def embed_query(self, _query):
                return [0.1, 0.2]

        obs = await _dense_retrieve(_Embed(), "q", 9901, 5)
        first, second = obs.results
        assert (first["original_text"], first["text_provenance"]) == (
            "第7条 原文",
            "parser_original",
        )
        assert second["original_text"] is None
        assert second["text_provenance"] is None
        assert result_text(first)[1] == "original"
        # 查询本身必须向 ES 请求 provenance 字段（删任一映射该断言即红）
        assert captured_queries, "dense 必须实际发起 ES 查询"
        assert {"original_text", "text_provenance"} <= set(captured_queries[0]["_source"])


class TestReportMeta:
    def test_report_records_mode_and_basis_counts(self, tmp_path):
        item = _item("a")
        result = _o_result("f.md", "第7条")
        runs = [
            ItemRun(
                item=item,
                outcome=evaluate_item(item, [result]),
                results=[result],
            )
        ]
        golden = GoldenSet(
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
            golden,
            runs,
            index="idx",
            org_id=9901,
            top_k=5,
            retriever_label="fake",
            provenance_mode="strict",
        )
        assert report["meta"]["provenance_mode"] == "strict"
        assert report["meta"]["text_basis_counts"] == {"original": 1}
        assert report["items"][0]["text_basis"] == "original"
        # 本批不扩张 CI 协议键（协议/基线变更单独立项）
        assert "provenance_mode" not in PROTOCOL_KEYS


class TestReportUsesOriginal:
    def test_report_defs_and_snippet_come_from_original(self, tmp_path):
        item = _item("a")
        # 诱饵：E 是摘要/改写文本，不含行首条款定义；O 才是合同原文。
        result = {
            "filename": "f.md",
            "content": "（摘要）付款为 30 天",
            "original_text": "第7条 付款期限为30天。",
            "text_provenance": "parser_original",
            "score": 0.9,
        }
        outcome = evaluate_item(item, [result])
        runs = [ItemRun(item=item, outcome=outcome, results=[result])]
        golden = GoldenSet(
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
            golden,
            runs,
            index="idx",
            org_id=9901,
            top_k=5,
            retriever_label="fake",
            provenance_mode="compat",
        )
        trimmed = report["items"][0]["results"][0]
        assert "第7条" not in result["content"], "诱饵 E 本身不得含定义位"
        assert trimmed["defs"] == ["第7条"]
        assert trimmed["snippet"].startswith("第7条")
        assert trimmed["defs"] == outcome.top1_defs


class TestBuildReportContract:
    def test_provenance_mode_is_required(self, tmp_path):
        item = _item("a")
        runs = [ItemRun(item=item, outcome=evaluate_item(item, []), results=[])]
        golden = GoldenSet(
            dataset_id="ds",
            version=1,
            description="",
            path=tmp_path / "x.json",
            corpus_dir=tmp_path,
            corpus_files=("f.md",),
            top_k=5,
            items=(item,),
        )
        with pytest.raises(TypeError):
            build_report(golden, runs, index="idx", org_id=9901, top_k=5, retriever_label="fake")


class TestSeedCommandPayload:
    @pytest.mark.asyncio
    async def test_seed_command_writes_original_and_provenance(self, tmp_path, monkeypatch):
        import app.core.config as config_mod
        import app.core.elasticsearch as es_mod
        from evals import runner

        (tmp_path / "f.md").write_text("第1条 正文一\n\n第2条 正文二\n", encoding="utf-8")
        golden = GoldenSet(
            dataset_id="ds",
            version=1,
            description="",
            path=tmp_path / "x.json",
            corpus_dir=tmp_path,
            corpus_files=("f.md",),
            top_k=5,
            items=(),
        )
        args = SimpleNamespace(org_id=9901, dry_run=False, recreate=False, embedding="fake")

        class _Indices:
            async def refresh(self, index=None):
                return None

        class _Client:
            def __init__(self, count: int):
                self._count = count
                self.indices = _Indices()

            async def count(self, **kwargs):
                return {"count": self._count}

            async def search(self, **kwargs):
                return {"aggregations": {"docs": {"value": 1}}}

            async def delete_by_query(self, **kwargs):
                return {"deleted": 0}

        captured: dict = {}

        async def _fake_bulk(actions):
            captured["actions"] = actions

        class _Embed:
            async def get_embeddings(self, texts):
                return [[0.1, 0.2] for _ in texts]

        async def _init_es():
            return None

        async def _get_es():
            return _Client(2)

        async def _close_es():
            return None

        monkeypatch.setattr(es_mod, "init_elasticsearch", _init_es)
        monkeypatch.setattr(es_mod, "get_index_error", lambda: None)
        monkeypatch.setattr(es_mod, "get_elasticsearch", _get_es)
        monkeypatch.setattr(es_mod, "close_elasticsearch", _close_es)
        monkeypatch.setattr(es_mod.ElasticsearchTools, "bulk_index_documents", _fake_bulk)
        monkeypatch.setattr(runner, "_require_embedding_service", lambda mode: _Embed())
        monkeypatch.setattr(
            config_mod.settings, "ELASTICSEARCH_INDEX_NAME", "docmind_knowledge_eval_tmp"
        )
        monkeypatch.setattr(config_mod.settings, "VECTOR_DIMENSION", 2)

        rc = await runner._cmd_seed_async(args, golden)

        assert rc == 0
        actions = captured["actions"]
        assert len(actions) == 2
        for action in actions:
            source = action["_source"]
            assert source["text_provenance"] == "parser_original"
            assert source["original_text"] == source["content"]
