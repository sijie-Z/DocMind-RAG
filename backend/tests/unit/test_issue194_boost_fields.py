"""issue #194 收口：``get_boost_fields()`` 只允许 ES mapping 中真实存在的字段。

旧表给 9 个 mapping / 索引写入都不存在的意图字段加权
（``title`` / ``list_content`` / ``definition`` / ``comparison`` / ``pros_cons`` /
``reason`` / ``result`` / ``summary`` / ``conclusion``）——``multi_match`` 对未映射
字段的权重恒为 0，是「看起来按意图调权、实际全部空气」的死配置，还让排查者
反复去调这些不存在的权重。现收敛为 mapping 真实字段 + 运行时白名单守卫，
并以本文件钉住不变式 ``set(boost 字段) ⊆ set(mapping.properties)``。

``section_title`` 是 mapping 字段，顶层写入由 #188（并行批次）落地；落地前它
同样是 0 分项，保留是为了 #188 合入后自动生效，而不是再造一条死配置。

变异点（改回/改坏后本文件应红）：
1. 任一意图表重新写回上述 9 个死字段之一 →
   ``test_intent_table_raw_fields_exist_in_mapping`` /
   ``test_dead_intent_fields_removed`` 红；
2. 从 ``_knowledge_index_mapping`` 删掉 ``section_title``/``filename`` →
   ``test_boost_fields_exist_in_es_mapping`` 红；
3. 删掉 ``BOOSTABLE_FIELDS`` 运行时守卫 → ``test_guard_drops_unlisted_fields`` 红；
4. 删掉「全部字段非法时回落默认字段」→
   ``test_guard_falls_back_when_all_fields_invalid`` 红；
5. 白名单加入未被任何意图使用的字段 → ``test_boostable_fields_have_no_dead_members`` 红；
6. 删除某个意图键 → ``test_intent_table_covers_all_intents`` 红。

边界说明：本文件只能钉住「boost 字段存在且被加权」，**不能**证明索引期
确有写入方（这正是 #194 的根因类别）；``section_title`` 的写侧覆盖随 #188
的验收补齐。
"""

from typing import get_args

from app.core.elasticsearch import _knowledge_index_mapping
from app.rag import query_processor as qp
from app.rag.query_processor import QueryIntentClassifier

ALL_INTENTS = [
    "factual",
    "procedural",
    "list",
    "definition",
    "comparison",
    "causal",
    "summary",
    "other",
]

# issue #194 点名的 9 个「mapping 里不存在、也无人写入」的意图字段。
DEAD_FIELDS = {
    "title",
    "list_content",
    "definition",
    "comparison",
    "pros_cons",
    "reason",
    "result",
    "summary",
    "conclusion",
}


def _field_names(fields: list[str]) -> set[str]:
    return {spec.split("^", 1)[0] for spec in fields}


def test_boost_fields_exist_in_es_mapping():
    """所有意图的 boost 字段都必须是 ES mapping 的真实属性。"""
    props = _knowledge_index_mapping()["mappings"]["properties"]
    for intent in ALL_INTENTS:
        names = _field_names(QueryIntentClassifier.get_boost_fields(intent))
        assert names, f"{intent} 没有任何 boost 字段"
        missing = names - set(props)
        assert not missing, f"{intent} boost 引用了未映射字段: {sorted(missing)}"


def test_intent_table_raw_fields_exist_in_mapping():
    """直接检查意图表原始项（绕过运行时守卫）。

    守卫是兜底而不是免死金牌：意图表本身就不允许出现未映射字段——
    否则每次调用都会走告警分支，等于把死配置藏进了运行时日志。
    """
    props = _knowledge_index_mapping()["mappings"]["properties"]
    for intent, specs in QueryIntentClassifier._INTENT_BOOST_FIELDS.items():
        missing = _field_names(specs) - set(props)
        assert not missing, f"{intent} 意图表引用未映射字段: {sorted(missing)}"


def test_dead_intent_fields_removed():
    """9 个死字段不得再出现在任何意图的 boost 表里（原始表 + 出口双查）。"""
    for intent, specs in QueryIntentClassifier._INTENT_BOOST_FIELDS.items():
        names = _field_names(specs)
        assert not (names & DEAD_FIELDS), f"{intent} 仍引用死字段: {sorted(names & DEAD_FIELDS)}"
    for intent in ALL_INTENTS:
        names = _field_names(QueryIntentClassifier.get_boost_fields(intent))
        assert not (names & DEAD_FIELDS), (
            f"{intent} 出口仍引用死字段: {sorted(names & DEAD_FIELDS)}"
        )


def test_boostable_fields_have_no_dead_members():
    """白名单里的每个字段都必须被至少一个意图使用（不留新的死配置）。"""
    used: set[str] = set()
    for intent in ALL_INTENTS:
        used |= _field_names(QueryIntentClassifier.get_boost_fields(intent))
    assert used <= qp.BOOSTABLE_FIELDS, (
        f"意图表出现白名单外字段: {sorted(used - qp.BOOSTABLE_FIELDS)}"
    )
    assert used >= qp.BOOSTABLE_FIELDS, (
        f"白名单存在无意图使用的死成员: {sorted(qp.BOOSTABLE_FIELDS - used)}"
    )


def test_intent_table_covers_all_intents():
    """意图表必须覆盖 ``QueryIntent`` 的全部取值；缺键会静默回落到 other。"""
    assert set(QueryIntentClassifier._INTENT_BOOST_FIELDS) == set(get_args(qp.QueryIntent))


def test_guard_drops_unlisted_fields(monkeypatch):
    """运行时守卫：意图表若被改出白名单，宁可不加权也不发未映射字段。"""
    monkeypatch.setitem(
        QueryIntentClassifier._INTENT_BOOST_FIELDS,
        "factual",
        ["chunk_text^2", "content^2", "title^1.5"],
    )
    assert QueryIntentClassifier.get_boost_fields("factual") == ["chunk_text^2", "content^2"]


def test_guard_falls_back_when_all_fields_invalid(monkeypatch):
    """全部字段被守卫剔除时回落安全默认——multi_match 的 fields 不能为空。"""
    monkeypatch.setitem(QueryIntentClassifier._INTENT_BOOST_FIELDS, "factual", ["title^1.5"])
    assert QueryIntentClassifier.get_boost_fields("factual") == ["chunk_text^2", "content^2"]


def test_boost_weights_parse_and_positive():
    """每个 boost 规格都能解析为「字段^正权重」，且同意图内字段不重复。"""
    for intent in ALL_INTENTS:
        specs = QueryIntentClassifier.get_boost_fields(intent)
        names = [spec.split("^", 1)[0] for spec in specs]
        assert len(names) == len(set(names)), f"{intent} 字段重复: {names}"
        for spec in specs:
            name, sep, weight = spec.partition("^")
            assert sep and name, f"{intent} 规格缺权重: {spec}"
            assert float(weight) > 0, f"{intent} 权重非正: {spec}"
