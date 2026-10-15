"""#208 第⑤步 CI 门禁的判别用例（无 ES / 无网络，秒级）。

覆盖：
1. 确定性伪嵌入：逐位可复现 + 算法指纹锁（改分桶/归一化/token 口径必红）
   + 向量臂接口与 OpenAI 客户端同形；
2. compare 门禁语义：floor/cap 的容差=0 边界、协议校验、fail-closed；
3. 基线工件锁定：入库的 baselines/ci_baseline.json 的 schema 与协议；
4. seed 协议的可测部分：--recreate 护栏、回读断言、fake 模式维度钉死；
5. 语料分块数（17+11=28）——CI seed 回读断言期望值的来源。
"""

import hashlib
import json
import os
import struct
from pathlib import Path

import pytest

from evals.chunking import split_corpus_file
from evals.deterministic_embedding import (
    CI_EMBEDDING_DIM,
    CI_EMBEDDING_ID,
    DeterministicEmbeddingClient,
    embed_text,
)
from evals.golden import load_golden_set
from evals.runner import (
    CI_METRIC_SPECS,
    CompareError,
    _apply_env,
    _recreate_guard,
    _seed_readback_error,
    build_parser,
    compare_report,
    main,
    stamp_baseline,
    validate_baseline,
)

_BACKEND = Path(__file__).resolve().parents[2]
_BASELINE = _BACKEND / "evals" / "baselines" / "ci_baseline.json"
_GOLDEN_V1 = _BACKEND / "evals" / "golden_set" / "clause_set_v1.json"

# 算法指纹：embed_text("《知识库服务管理办法》第3条规定了什么？") 的
# sha256(struct.pack("<256d", *vec))——任何数值行为变化都必须显式更新它，
# 等价于「必须意识到 CI 基线语义变化」。
_EMBED_DIGEST = "7267798fbb44e0dfb7a17f15974dd0dbee6238f8252aaf6be52579a8c7f88be6"


def _report(**overrides) -> dict:
    """一份合法的 CI 模式报告（测试用）；overrides 形如 'aggregates.mrr' / 'meta.top_k'。"""
    report = {
        "meta": {
            "embedding_mode": "fake",
            "top_k": 5,
            "dataset_id": "docmind_clause_v1",
            "dataset_version": 1,
            "corpus_chunks": {"a.md": 17, "b.md": 11},
            "index": "docmind_knowledge_eval_ci_v1",
            "es_version": "8.11.0",
            "generated_at": "2026-10-09T00:00:00+00:00",
        },
        "aggregates": {
            "counts": {"errors": 0},
            "clause_hit@1": 0.8,
            "clause_hit@3": 0.84,
            "clause_hit@5": 0.84,
            "near_miss@5": 0.16666666666666666,
            "mrr": 0.82,
            "empty_accuracy": 1.0,
        },
    }
    for path, value in overrides.items():
        head, _, tail = path.partition(".")
        report[head][tail] = value
    return report


def _stamped_baseline() -> dict:
    return stamp_baseline(_report())


def _failed_names(rows: list[dict]) -> list[str]:
    return [r["name"] for r in rows if r["verdict"] != "pass"]


# ---------------------------------------------------------------------------
# 1. 确定性伪嵌入
# ---------------------------------------------------------------------------


class TestDeterministicEmbedding:
    def test_shape_norm_and_zero(self):
        vec = embed_text("第3条 知识库数据按敏感程度分级")
        assert len(vec) == CI_EMBEDDING_DIM
        assert abs(sum(x * x for x in vec) - 1.0) < 1e-12
        empty = [0.0] * CI_EMBEDDING_DIM
        assert embed_text("") == empty
        assert embed_text("  \n\t ") == empty

    def test_bitwise_determinism(self):
        text = "《知识库服务管理办法》第3条规定了什么？"
        assert embed_text(text) == embed_text(text)  # float 逐位相等
        # \n 与空白等价（与 retriever/_embed_texts 的 \n→空格 替换口径一致）
        assert embed_text("第7条\n条文内容") == embed_text("第7条 条文内容")

    def test_algorithm_digest_lock(self):
        vec = embed_text("《知识库服务管理办法》第3条规定了什么？")
        digest = hashlib.sha256(struct.pack(f"<{len(vec)}d", *vec)).hexdigest()
        assert digest == _EMBED_DIGEST, (
            "伪嵌入的数值行为变了——CI 基线语义随之变化，必须先意识到并同步 "
            "baselines/ci_baseline.json（显式 PR 行为），再更新本指纹"
        )
        assert CI_EMBEDDING_ID == "hash-bigram-char-v1"
        assert CI_EMBEDDING_DIM == 256

    def test_shared_lexical_content_ranks_higher(self):
        query = "审计日志保留多久？"
        near = "第9条 审计日志保留 180 天，到期自动归档。"
        far = "第14条 服务故障后 1 小时内必须完成恢复。"

        def cos(x, y):
            return sum(a * b for a, b in zip(x, y, strict=True))

        assert cos(embed_text(query), embed_text(near)) > cos(embed_text(query), embed_text(far))

    @pytest.mark.asyncio
    async def test_client_interface_matches_consumer_shape(self):
        client = DeterministicEmbeddingClient()
        # 单条输入（retriever._embed 的用法）
        resp = await client.embeddings.create(input="第3条", model="any")
        assert resp.data[0].embedding == embed_text("第3条")
        # 批量输入（runner._embed_texts 的用法）
        resp2 = await client.embeddings.create(input=["第3条", "第7条"], model="any")
        assert len(resp2.data) == 2
        assert resp2.data[1].embedding == embed_text("第7条")
        await client.close()


# ---------------------------------------------------------------------------
# 2. compare 门禁语义（容差 = 0）
# ---------------------------------------------------------------------------


class TestCompareGate:
    def test_equal_to_baseline_passes(self):
        rows = compare_report(_report(), _stamped_baseline())
        assert _failed_names(rows) == []
        assert len(rows) == len(CI_METRIC_SPECS)

    def test_floor_regression_fails(self):
        report = _report(**{"aggregates.clause_hit@5": 0.80})  # 0.84 跌 1/25
        rows = compare_report(report, _stamped_baseline())
        assert _failed_names(rows) == ["clause_hit@5"]
        bad = next(r for r in rows if r["name"] == "clause_hit@5")
        assert bad["kind"] == "floor" and "越界" in bad["note"]

    def test_floor_boundary_is_inclusive(self):
        # 容差 0 的口径：恰好等于基线 = 通过（>= 而非 >）
        rows = compare_report(_report(**{"aggregates.mrr": 0.82}), _stamped_baseline())
        assert _failed_names(rows) == []

    def test_cap_regression_fails(self):
        report = _report(**{"aggregates.near_miss@5": 5 / 24})  # 4/24 → 5/24
        rows = compare_report(report, _stamped_baseline())
        assert _failed_names(rows) == ["near_miss@5"]

    def test_cap_boundary_is_inclusive(self):
        rows = compare_report(
            _report(**{"aggregates.near_miss@5": 0.16666666666666666}), _stamped_baseline()
        )
        assert _failed_names(rows) == []

    def test_improvements_are_allowed(self):
        report = _report(**{"aggregates.clause_hit@5": 0.92, "aggregates.near_miss@5": 0.0})
        rows = compare_report(report, _stamped_baseline())
        assert _failed_names(rows) == []

    def test_errors_must_be_zero(self):
        report = _report()
        report["aggregates"]["counts"] = {"errors": 1}
        rows = compare_report(report, _stamped_baseline())
        assert _failed_names(rows) == ["errors"]

    def test_none_metric_fails_closed(self):
        rows = compare_report(_report(**{"aggregates.empty_accuracy": None}), _stamped_baseline())
        assert _failed_names(rows) == ["empty_accuracy"]

    @pytest.mark.parametrize(
        "override",
        [
            {"meta.embedding_mode": "live"},
            {"meta.top_k": 3},
            {"meta.dataset_id": "other"},
            {"meta.dataset_version": 2},
            {"meta.corpus_chunks": {"a.md": 18, "b.md": 11}},
        ],
    )
    def test_protocol_mismatch_rejected(self, override):
        with pytest.raises(CompareError):
            compare_report(_report(**override), _stamped_baseline())

    def test_missing_metric_is_structural_error(self):
        report = _report()
        del report["aggregates"]["clause_hit@3"]
        with pytest.raises(CompareError):
            compare_report(report, _stamped_baseline())

    def test_missing_meta_is_structural_error(self):
        report = _report()
        del report["meta"]["embedding_mode"]
        with pytest.raises(CompareError):
            compare_report(report, _stamped_baseline())


class TestBaselineSchema:
    def test_stamp_roundtrip_validates(self):
        validate_baseline(_stamped_baseline())

    def test_missing_protocol_key_rejected(self):
        baseline = _stamped_baseline()
        del baseline["protocol"]["top_k"]
        with pytest.raises(CompareError):
            validate_baseline(baseline)

    def test_missing_corpus_chunks_rejected(self):
        baseline = _stamped_baseline()
        del baseline["protocol"]["corpus_chunks"]
        with pytest.raises(CompareError):
            validate_baseline(baseline)

    def test_metric_set_is_exact(self):
        baseline = _stamped_baseline()
        del baseline["metrics"]["mrr"]
        with pytest.raises(CompareError):
            validate_baseline(baseline)
        baseline = _stamped_baseline()
        baseline["metrics"]["surprise"] = {"kind": "floor", "value": 1.0}
        with pytest.raises(CompareError):
            validate_baseline(baseline)

    def test_wrong_kind_rejected(self):
        baseline = _stamped_baseline()
        baseline["metrics"]["clause_hit@1"]["kind"] = "cap"
        with pytest.raises(CompareError):
            validate_baseline(baseline)

    def test_non_numeric_value_rejected(self):
        baseline = _stamped_baseline()
        baseline["metrics"]["mrr"]["value"] = "0.82"
        with pytest.raises(CompareError):
            validate_baseline(baseline)

    def test_stamp_refuses_errors_and_none(self):
        report = _report()
        report["aggregates"]["counts"] = {"errors": 2}
        with pytest.raises(CompareError):
            stamp_baseline(report)
        with pytest.raises(CompareError):
            stamp_baseline(_report(**{"aggregates.mrr": None}))


class TestCompareCli:
    def _write(self, tmp_path, report_text: str, baseline: dict | None = None):
        rp = tmp_path / "report.json"
        rp.write_text(report_text, encoding="utf-8")
        bp = tmp_path / "baseline.json"
        if baseline is not None:
            bp.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
        return rp, bp

    def test_green_exit_code_zero(self, tmp_path):
        rp, bp = self._write(tmp_path, json.dumps(_report()), _stamped_baseline())
        assert main(["compare", "--report", str(rp), "--baseline", str(bp)]) == 0

    def test_regression_exit_code_one(self, tmp_path):
        bad = _report(**{"aggregates.clause_hit@1": 0.6})
        rp, bp = self._write(tmp_path, json.dumps(bad), _stamped_baseline())
        assert main(["compare", "--report", str(rp), "--baseline", str(bp)]) == 1

    def test_failclosed_missing_report(self, tmp_path):
        _, bp = self._write(tmp_path, "{}", _stamped_baseline())
        assert main(["compare", "--report", str(tmp_path / "nope.json"), "--baseline", str(bp)]) == 2

    def test_failclosed_corrupt_report(self, tmp_path):
        rp, bp = self._write(tmp_path, "not json at all", _stamped_baseline())
        assert main(["compare", "--report", str(rp), "--baseline", str(bp)]) == 2

    def test_failclosed_corrupt_baseline(self, tmp_path):
        rp, bp = self._write(tmp_path, json.dumps(_report()), None)
        bp.write_text("{", encoding="utf-8")
        assert main(["compare", "--report", str(rp), "--baseline", str(bp)]) == 2

    def test_update_baseline_writes_then_passes(self, tmp_path):
        rp, bp = self._write(tmp_path, json.dumps(_report()), None)  # 基线不存在 → 新建
        assert (
            main(
                [
                    "compare",
                    "--report",
                    str(rp),
                    "--baseline",
                    str(bp),
                    "--update-baseline",
                ]
            )
            == 0
        )
        assert bp.is_file()
        assert main(["compare", "--report", str(rp), "--baseline", str(bp)]) == 0

    def test_update_baseline_refuses_errors(self, tmp_path):
        report = _report()
        report["aggregates"]["counts"] = {"errors": 1}
        rp, bp = self._write(tmp_path, json.dumps(report), _stamped_baseline())
        before = bp.read_text(encoding="utf-8")
        assert (
            main(
                [
                    "compare",
                    "--report",
                    str(rp),
                    "--baseline",
                    str(bp),
                    "--update-baseline",
                ]
            )
            == 2
        )
        assert bp.read_text(encoding="utf-8") == before  # 拒绝时不得改写


# ---------------------------------------------------------------------------
# 3. seed 协议（可测部分）与 CLI 面
# ---------------------------------------------------------------------------


class TestSeedProtocol:
    def test_recreate_guard_only_allows_eval_indexes(self):
        assert _recreate_guard("docmind_knowledge_eval_ci_v1") is None
        assert _recreate_guard("docmind_knowledge_eval_v1") is None
        msg = _recreate_guard("docmind_knowledge")
        assert msg is not None
        assert "docmind_knowledge" in msg and "eval" in msg

    def test_readback_assert(self):
        assert _seed_readback_error(28, 28) is None
        msg = _seed_readback_error(28, 27)
        assert msg is not None and "27" in msg and "28" in msg

    def test_fake_mode_pins_vector_dimension(self, monkeypatch):
        monkeypatch.setenv("VECTOR_DIMENSION", "1024")
        args = build_parser().parse_args(["seed", "--recreate", "--embedding", "fake"])
        _apply_env(args)
        assert os.environ["VECTOR_DIMENSION"] == str(CI_EMBEDDING_DIM)
        # live 模式必须原样保留操作者设置（不得覆盖）
        monkeypatch.setenv("VECTOR_DIMENSION", "1024")
        _apply_env(build_parser().parse_args(["run"]))
        assert os.environ["VECTOR_DIMENSION"] == "1024"

    def test_embedding_choices(self):
        assert build_parser().parse_args(["run"]).embedding == "live"
        with pytest.raises(SystemExit):
            build_parser().parse_args(["run", "--embedding", "bogus"])

    def test_compare_default_paths(self):
        args = build_parser().parse_args(["compare"])
        assert args.report == "evals/reports/ci/latest.json"
        assert args.baseline.endswith("ci_baseline.json")
        assert args.update_baseline is False


# ---------------------------------------------------------------------------
# 4. 入库工件与语料分块数
# ---------------------------------------------------------------------------


class TestCommittedArtifacts:
    def test_committed_baseline_schema_and_protocol(self):
        baseline = validate_baseline(json.loads(_BASELINE.read_text(encoding="utf-8")))
        proto = baseline["protocol"]
        assert proto["embedding_mode"] == "fake"
        assert proto["top_k"] == 5
        assert proto["embedding_impl"].startswith(CI_EMBEDDING_ID)
        gs = load_golden_set(_GOLDEN_V1)
        assert proto["dataset_id"] == gs.dataset_id
        assert proto["dataset_version"] == gs.version

    def test_committed_baseline_corpus_chunks_match_real_corpus(self):
        baseline = json.loads(_BASELINE.read_text(encoding="utf-8"))
        gs = load_golden_set(_GOLDEN_V1)
        actual = {
            f: len(split_corpus_file((gs.corpus_dir / f).read_text(encoding="utf-8")))
            for f in gs.corpus_files
        }
        assert baseline["protocol"]["corpus_chunks"] == actual

    def test_committed_baseline_values_are_sane(self):
        baseline = json.loads(_BASELINE.read_text(encoding="utf-8"))
        for name, _kind, _section in CI_METRIC_SPECS:
            value = baseline["metrics"][name]["value"]
            if name != "errors":
                assert 0.0 <= float(value) <= 1.0
        assert baseline["metrics"]["errors"]["value"] == 0  # errors cap = 0
        assert baseline["metrics"]["empty_accuracy"]["value"] == 1.0  # 负例必须全空

    def test_corpus_chunk_counts_pinned(self):
        """CI seed 回读断言的期望值来源：语料分块数写死（改语料=改协议，必显式）。"""
        gs = load_golden_set(_GOLDEN_V1)
        counts = {
            f: len(split_corpus_file((gs.corpus_dir / f).read_text(encoding="utf-8")))
            for f in gs.corpus_files
        }
        assert counts == {
            "01-knowledge-base-policy.md": 17,
            "02-incident-response-handbook.md": 11,
        }
        assert sum(counts.values()) == 28
