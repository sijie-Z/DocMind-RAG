"""#197 判别测试：page_number 从解析页偏移贯通到切分 / 落库 / 检索字段。

修前缺陷（分诊报告 #197，origin/develop 6db5fa61 实测）：
1. PDF 解析把页信息收集进文档级 ``metadata["pages"]``，但拼接 ``text_content``
   时不记录字符偏移，chunk 构造完全拿不到页码；
2. ``_create_contextual_chunks(text_content, metadata)`` 的 metadata 形参零引用
   （页信息传不进 chunk 构造）——本轮删除该死参数，改为显式 ``page_ranges``；
3. 切分器不产出 ``start_pos``/``end_pos``；``document_parser.py`` 与
   ``doc_processor.py`` 都从 ``metadata.get("page_number")`` 取值 → 恒 None；
4. ES mapping 无顶层 ``page_number``、检索 ``_source`` 白名单不含、
   ``_post_process`` 与 ``build_sources_list`` 不透传。

修法（分诊推荐，与 #188 的结构字段互不重复）：
- PDF 拼接期维护 ``[(page_no, start_offset, end_offset)]``，传给切分器；
- 切分器按句级源偏移产出 ``start_pos``/``end_pos``（覆盖合并 / 超长切分后的
  精确投影），并按起始偏移映射 ``page_number``；
- parser chunk 顶层携带 ``start_pos``/``end_pos``、metadata 携带 ``page_number``，
  ``document_chunks.page_number`` 落库值不再恒 NULL；
- ES mapping 增加顶层 ``page_number``（integer），doc_processor 写入；
  检索白名单 / ``_post_process`` / ``build_sources_list`` 逐层透传；
  非 PDF 格式没有页偏移 → ``page_number`` 保持 None（不伪造）。

变异点（红→绿判别）：
- 切分器不产出 start_pos/end_pos、或页码映射用错偏移（如用 end_pos、用分区
  起始）→ ``TestChunkerPositions`` 红；
- PDF 解析不记录页偏移 / 不把 page_ranges 传给切分器 → ``TestPdfPageOffsets`` 红；
- 合并小尾块不更新 end_pos → ``test_merge_tail_extends_end_pos`` 红；
- ``_build_es_source`` 不写顶层 page_number、mapping 缺字段 →
  ``TestDocumentProcessorPageNumber`` 红；
- ``_build_es_source`` 不写顶层 page_number、mapping 缺字段 →
  ``TestDocumentProcessorPageNumber`` 红；DB 侧 ``page_number`` / ``start_pos`` /
  ``end_pos`` 的落库透传在基线即存在，用例同时锁住该既有行为（guard 断言，
  不是本轮判别点）；
- 检索白名单缺 page_number / ``_post_process`` 或 ``build_sources_list`` 不透传 →
  ``TestRetrievalPassthrough`` 红；
- ``knowledge_service.search_knowledge`` 重排结果时丢弃 page_number →
  ``TestKnowledgeServicePassthrough`` 红（端点 ``r.get("page_number")`` 会恒为 None）；
- 切分器句段构造对越界 split index 不设防 → ``test_extra_provider_vectors_do_not_crash`` 红；
- 非 PDF 文本被伪造出页码 → ``test_non_pdf_chunks_have_no_page_number`` 红
  （防伪造 guard；基线上因无 page_number 键而绿，不承担判别职责）。

真实 ES / MySQL 数据态（重索引后 ``SELECT COUNT(page_number)`` 与 ES 顶层字段）
标「待栈恢复复测」：本文件全部为单元级判别，不依赖任何服务。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

import app.rag.retriever as retriever_mod
import app.worker.doc_processor as doc_processor_mod
from app.core.elasticsearch import _knowledge_index_mapping
from app.rag.retriever import HybridRetriever
from app.services.document_parser import DocumentParser
from app.services.semantic_chunker import SemanticChunker
from app.worker.doc_processor import DocumentProcessor

# 两页 PDF 风格文本：各含一个条款分区（第二条在行首 → 独立分区），
# 总长 > 500 触发分区切分路径。每条分区的句数 < 5，不会走 embedding；
# 仍按既有测试惯例 patch embedding_service，避免共享单例被意外调用。
PAGE1 = "第一条 总则\n" + "为规范数据安全管理，本制度适用于全部部门。" * 24 + "\n"
PAGE2 = "第二条 附则\n本制度自发布之日起施行。\n"
TEXT = PAGE1 + PAGE2
PAGE_RANGES = [(1, 0, len(PAGE1)), (2, len(PAGE1), len(TEXT))]


def _identical_embeddings():
    return [np.array([1.0, 0.0])] * 200


def _chunk_with_page(page: int = 7) -> dict:
    return {
        "chunk_text": "【文档背景：摘要】\n\n第3条 数据分级\n数据分为三级。",
        "original_text": "第3条 数据分级\n数据分为三级。",
        "chunk_index": 2,
        "chunk_length": 40,
        "start_pos": 120,
        "end_pos": 145,
        "metadata": {"section_title": "第3条 数据分级", "page_number": page},
    }


def _doc() -> SimpleNamespace:
    return SimpleNamespace(
        id="doc-1",
        organization_id=1,
        filename="policy.pdf",
        file_type="pdf",
        file_size=123,
        created_at=None,
        uploaded_by=7,
    )


# ══════════════════════════════════════════════════════════════════════════
# 1 · 切分器：源偏移 + 页码映射
# ══════════════════════════════════════════════════════════════════════════


class TestChunkerPositions:
    @pytest.mark.asyncio
    async def test_chunks_carry_source_spans_and_page_numbers(self):
        chunker = SemanticChunker()
        text = TEXT
        page1_end = len(PAGE1)

        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=_identical_embeddings())
            chunks = await chunker.split_text(text, page_ranges=PAGE_RANGES)

        assert len(chunks) == 2, f"两个条款分区应产出两个 chunk，实际 {len(chunks)}"
        c1, c2 = chunks

        for ch in chunks:
            meta = ch["metadata"]
            assert isinstance(meta.get("start_pos"), int), (
                f"chunk 必须携带 start_pos（#197 修前完全缺失），实际 {meta}"
            )
            assert isinstance(meta.get("end_pos"), int), (
                f"chunk 必须携带 end_pos，实际 {meta}"
            )
            assert meta["start_pos"] < meta["end_pos"]

        # 第一块从文首起，页码 1；第二块从「第二条」行首起，页码 2
        assert c1["metadata"]["start_pos"] == 0
        assert c1["metadata"]["page_number"] == 1, (
            f"第一页的块页码必须是 1，实际 {c1['metadata'].get('page_number')}"
        )
        assert c1["metadata"]["end_pos"] <= page1_end, "第一块不得跨到第二页之后"
        assert c2["metadata"]["start_pos"] == page1_end
        assert c2["metadata"]["page_number"] == 2, (
            f"第二页的块页码必须是 2，实际 {c2['metadata'].get('page_number')}"
        )

        # 偏移必须真的指向源文：第二块文本与源文切片逐字一致
        s, e = c2["metadata"]["start_pos"], c2["metadata"]["end_pos"]
        assert text[s:e] == c2["text"], (
            "start_pos/end_pos 必须精确指向该 chunk 在原文中的位置"
        )

    @pytest.mark.asyncio
    async def test_overlong_split_pieces_keep_exact_spans(self):
        """超长块被连续切分后，每个片段的 span 仍是源文中的真实位置。"""
        chunker = SemanticChunker()
        sentences = [f"第{i}句讲述数据安全管理要求与实施细则。" for i in range(80)]
        text = " ".join(sentences) + "\n"
        assert len(text) > 1200
        page1_end = len(text) // 2

        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=_identical_embeddings())
            chunks = await chunker.split_text(
                text, page_ranges=[(1, 0, page1_end), (2, page1_end, len(text))]
            )

        assert len(chunks) >= 2, "超长块必须被切成多个片段"
        prev_end = -1
        for ch in chunks:
            s, e = ch["metadata"]["start_pos"], ch["metadata"]["end_pos"]
            assert 0 <= s < e <= len(text)
            assert s >= prev_end, "片段 span 必须按源文顺序、不得重叠"
            prev_end = e
            # 片段文本是若干整句的拼接（句间空白被 strip），归一化后必须仍是
            # 其源文切片归一化后的子串——span 错位（如按拼接文本线性映射）会红。
            norm_piece = "".join(ch["text"].split())
            norm_src = "".join(text[s:e].split())
            assert norm_piece and norm_piece in norm_src, (
                f"span {s}:{e} 与片段文本不对应：{ch['text'][:30]!r}"
            )
            expected_page = 1 if s < page1_end else 2
            assert ch["metadata"]["page_number"] == expected_page, (
                f"页码必须按起始偏移映射（start={s}, 页边界={page1_end}）"
            )

    @pytest.mark.asyncio
    async def test_non_pdf_chunks_have_no_page_number(self):
        """没有页偏移的格式不得伪造页码（防伪造 guard；基线因无该键而绿）。"""
        chunker = SemanticChunker()
        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=_identical_embeddings())
            chunks = await chunker.split_text(TEXT)
        assert chunks
        assert all(ch["metadata"].get("page_number") is None for ch in chunks), (
            "未提供 page_ranges 时 page_number 必须保持 None（不伪造）"
        )

    @pytest.mark.asyncio
    async def test_short_text_maps_page_from_start_offset(self):
        """短文本（<500 字）走单 chunk 路径：页码取起始偏移所在页（跨页归起始页）。"""
        chunker = SemanticChunker()
        text = "短文本。" * 50  # ~200 字，< 500
        boundary = len(text) // 2
        chunks = await chunker.split_text(
            text, page_ranges=[(1, 0, boundary), (2, boundary, len(text))]
        )
        assert len(chunks) == 1
        meta = chunks[0]["metadata"]
        assert meta["start_pos"] == 0
        assert meta["page_number"] == 1, (
            "跨页的单 chunk 必须归起始页（#197 约定）"
        )
        assert meta["end_pos"] == len(text)

    @pytest.mark.asyncio
    async def test_window_budget_fallback_keeps_spans(self):
        """>120 滑动窗口的长度切分回退路径也必须产出源区间与页码（不调用 embedding）。"""
        chunker = SemanticChunker()
        text = "".join(f"第{i}句说明文字。" for i in range(130))
        boundary = len(text) // 2

        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(
                side_effect=AssertionError("窗口预算回退路径不得调用 embedding")
            )
            chunks = await chunker.split_text(
                text, page_ranges=[(1, 0, boundary), (2, boundary, len(text))]
            )

        assert len(chunks) == 2, "回退路径按长度二分"
        assert chunks[0]["metadata"]["start_pos"] == 0
        assert chunks[0]["metadata"]["page_number"] == 1
        assert chunks[1]["metadata"]["start_pos"] >= boundary, (
            "右半段的起始偏移必须落在第二页"
        )
        assert chunks[1]["metadata"]["page_number"] == 2
        for ch in chunks:
            s, e = ch["metadata"]["start_pos"], ch["metadata"]["end_pos"]
            assert 0 <= s < e <= len(text)

    @pytest.mark.asyncio
    async def test_embedding_failure_keeps_full_span(self):
        """embedding 失败的整段回退仍必须给出完整源区间（页码 1）。"""
        chunker = SemanticChunker()
        text = "".join(f"这是第{i}句普通的说明文字。" for i in range(60))
        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(side_effect=Exception("API down"))
            chunks = await chunker.split_text(
                text, page_ranges=[(1, 0, len(text))]
            )
        assert len(chunks) == 1
        meta = chunks[0]["metadata"]
        assert (meta["start_pos"], meta["end_pos"]) == (0, len(text))
        assert meta["page_number"] == 1

    @pytest.mark.asyncio
    async def test_extra_provider_vectors_do_not_crash(self):
        """provider 返回的向量数多于窗口数时，越界 split index 不得抛 IndexError。

        基线实现用切片 ``sentences[start_idx:idx+1]``——越界时得到空串并跳过；
        #197 的句段构造若对空 parts 直接取 ``segments[0]`` 会变成硬失败。
        """
        chunker = SemanticChunker()
        text = "".join(f"这是第{i}句普通的说明文字。" for i in range(60))
        # 200 个向量 → 199 个距离；在 99/100/149/150 处制造断点（索引越界）。
        vectors = [np.array([1.0, 0.0]) for _ in range(200)]
        for idx in (100, 150):
            vectors[idx] = np.array([0.0, 1.0])

        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=vectors)
            chunks = await chunker.split_text(text, page_ranges=[(1, 0, len(text))])

        assert chunks, "越界断点不得让整篇切分失败"
        assert "".join(ch["text"] for ch in chunks) == text
        for ch in chunks:
            s, e = ch["metadata"]["start_pos"], ch["metadata"]["end_pos"]
            assert 0 <= s < e <= len(text)

    def test_merge_tail_extends_end_pos(self):
        chunker = SemanticChunker()
        chunks = [
            {"text": "A" * 250, "metadata": {
                "section_title": "第一条 总则", "_partition": 0,
                "start_pos": 0, "end_pos": 250,
            }},
            {"text": "短尾块", "metadata": {
                "section_title": "第一条 总则", "_partition": 0,
                "start_pos": 300, "end_pos": 303,
            }},
        ]
        out = chunker._merge_small_tails(chunks)
        assert len(out) == 1
        assert out[0]["metadata"]["end_pos"] == 303, (
            "小尾块并入前块后 end_pos 必须延伸到尾块末尾（修前不更新）"
        )
        assert out[0]["metadata"]["start_pos"] == 0


# ══════════════════════════════════════════════════════════════════════════
# 2 · PDF 解析：页偏移记录并写入 chunk metadata
# ══════════════════════════════════════════════════════════════════════════


class _FakePage:
    def __init__(self, text: str):
        self._text = text

    def extract_text(self):
        return self._text

    def extract_tables(self):
        return []


class _FakePdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestPdfPageOffsets:
    @pytest.mark.asyncio
    async def test_pdf_chunks_get_page_numbers_from_parser(self, monkeypatch):
        import app.services.document_parser as parser_mod

        monkeypatch.setattr(
            parser_mod.pdfplumber,
            "open",
            lambda _path: _FakePdf([_FakePage(PAGE1), _FakePage(PAGE2)]),
        )

        parser = DocumentParser()
        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=_identical_embeddings())
            result = await parser._parse_pdf("fake.pdf")

        chunks = result["chunks"]
        assert len(chunks) == 2
        by_page = {}
        for ch in chunks:
            page = ch["metadata"].get("page_number")
            assert page is not None, (
                "PDF chunk 的 metadata.page_number 不得为 None（#197 修前恒 None）"
            )
            by_page.setdefault(page, []).append(ch)
            assert isinstance(ch.get("start_pos"), int)
            assert isinstance(ch.get("end_pos"), int)

        assert set(by_page) == {1, 2}, (
            f"两页文本的 chunk 页码应覆盖 {{1, 2}}，实际 {sorted(by_page)}"
        )
        assert by_page[1][0]["original_text"].startswith("第一条"), "第一页块文本不符"
        assert by_page[2][0]["original_text"].startswith("第二条"), "第二页块文本不符"

    @pytest.mark.asyncio
    async def test_non_pdf_parse_keeps_page_number_none(self):
        """TXT 等无页概念的格式：start_pos/end_pos 有值，page_number 保持 None。"""
        parser = DocumentParser()
        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=_identical_embeddings())
            result = await parser._create_contextual_chunks(TEXT)
        assert result
        assert all(c["metadata"].get("page_number") is None for c in result)
        assert all(isinstance(c.get("start_pos"), int) for c in result)

    @pytest.mark.asyncio
    async def test_empty_pdf_page_gets_no_chunk(self, monkeypatch):
        """空白页（无正文）不能成为任何 chunk 的页码：其后的正文归下一页。"""
        import app.services.document_parser as parser_mod

        monkeypatch.setattr(
            parser_mod.pdfplumber,
            "open",
            lambda _path: _FakePdf([_FakePage(PAGE1), _FakePage(""), _FakePage(PAGE2)]),
        )

        parser = DocumentParser()
        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=_identical_embeddings())
            result = await parser._parse_pdf("fake.pdf")

        pages = {ch["metadata"]["page_number"] for ch in result["chunks"]}
        assert pages == {1, 3}, (
            f"空白页不得被任何 chunk 引用（第二页无正文），实际页码集合 {sorted(pages)}"
        )


# ══════════════════════════════════════════════════════════════════════════
# 3 · 索引侧：mapping + ES _source + 落库
# ══════════════════════════════════════════════════════════════════════════


class TestDocumentProcessorPageNumber:
    def test_mapping_has_top_level_page_number(self):
        props = _knowledge_index_mapping()["mappings"]["properties"]
        assert "page_number" in props, "ES mapping 必须新增顶层 page_number 字段"
        assert props["page_number"]["type"] == "integer", (
            "page_number 必须是整型（按页过滤/排序都需要数值类型）"
        )

    def test_build_es_source_carries_page_number(self):
        source = DocumentProcessor._build_es_source(_doc(), "cid-1", 2, _chunk_with_page(7))
        assert source["page_number"] == 7, (
            "ES _source 顶层必须携带 page_number（修前只在 metadata.enabled=false 里）"
        )
        assert source["metadata"]["page_number"] == 7
        # #188 既有字段不得被本轮改动破坏
        assert source["section_title"] == "第3条 数据分级"

    @pytest.mark.asyncio
    async def test_replace_document_state_persists_page_and_positions(self, monkeypatch):
        captured: dict = {}

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, *args, **kwargs):
                return None

            def add_all(self, rows):
                captured["rows"] = list(rows)

            async def commit(self):
                return None

        class _FakeES:
            async def delete_by_query(self, **kwargs):
                return None

        async def fake_create_index():
            return None

        async def fake_get_es():
            return _FakeES()

        async def fake_bulk(actions):
            captured["actions"] = list(actions)

        async def fake_verify(*args, **kwargs):
            return None

        async def fake_clear_for_org(self, org_id):
            return None

        from app.rag import cache as cache_mod

        monkeypatch.setattr(doc_processor_mod, "AsyncSessionLocal", lambda: _FakeSession())
        monkeypatch.setattr(doc_processor_mod, "create_index_if_not_exists", fake_create_index)
        monkeypatch.setattr(doc_processor_mod, "get_elasticsearch", fake_get_es)
        monkeypatch.setattr(
            doc_processor_mod.ElasticsearchTools,
            "bulk_index_documents",
            staticmethod(fake_bulk),
        )
        monkeypatch.setattr(doc_processor_mod, "_verify_indexed", fake_verify)
        monkeypatch.setattr(cache_mod.RetrievalCache, "clear_for_org", fake_clear_for_org)
        monkeypatch.setattr(cache_mod.SemanticCache, "clear_for_org", fake_clear_for_org)

        processor = DocumentProcessor()
        await processor._replace_document_state(_doc(), [_chunk_with_page(7)], [[0.1, 0.2]])

        row = captured["rows"][0]
        assert row.page_number == 7, (
            "document_chunks.page_number 必须落库 7（#197 修前恒 NULL）"
        )
        assert row.start_pos == 120 and row.end_pos == 145, (
            "start_pos/end_pos 也必须随 chunk 落库（修前同样取不到）"
        )
        assert captured["actions"][0]["_source"]["page_number"] == 7


# ══════════════════════════════════════════════════════════════════════════
# 4 · 检索侧：白名单 / post-process / 来源卡片透传
# ══════════════════════════════════════════════════════════════════════════


class TestRetrievalPassthrough:
    @pytest.mark.asyncio
    async def test_source_whitelist_includes_page_number(self, monkeypatch):
        captured: list[dict] = []

        async def fake_search(es_query):
            captured.append(es_query)
            return {"hits": {"hits": []}}

        monkeypatch.setattr(retriever_mod.ElasticsearchTools, "search_documents", fake_search)

        retriever = HybridRetriever(openai_client=None)
        await retriever._keyword_hits("数据安全管理要求", [], candidate_size=10)

        assert captured, "关键词臂必须发出 ES 查询"
        for q in captured:
            assert "page_number" in (q.get("_source") or []), (
                "检索 _source 白名单必须包含 page_number，否则命中结果拿不到页码"
            )

    def test_post_process_carries_page_number(self):
        hit = {
            "_id": "doc01_c11",
            "_score": 0.5,
            "_source": {
                "content": "数据安全分级要求：数据分为一般、重要和核心三级。",
                "filename": "01.pdf",
                "document_id": "doc01",
                "page_number": 42,
                "section_title": "第3条 数据分级",
                "clause_no": [3],
                "section_no": [],
                "chunk_index": 2,
            },
            "has_keyword": True,
            "keyword_rank": 1,
        }
        docs = HybridRetriever()._post_process("数据安全分级", [hit], top_k=1)
        assert docs and docs[0]["page_number"] == 42, (
            "post-process 输出必须透传 page_number 供下游来源/引用展示"
        )

    def test_build_sources_list_exposes_page_number(self):
        from app.services.chat_service import build_sources_list

        sources = build_sources_list([{
            "filename": "policy.pdf",
            "page_number": 42,
            "document_id": "doc01",
            "text": "正文",
        }])
        assert sources[0]["pageNumber"] == 42, (
            "来源卡片必须携带 pageNumber（前端「第 N 页」的真实数据来源）"
        )

    def test_build_sources_list_page_number_none_when_absent(self):
        from app.services.chat_service import build_sources_list

        sources = build_sources_list([{
            "filename": "notes.txt",
            "document_id": "doc02",
            "text": "正文",
        }])
        assert sources[0]["pageNumber"] is None, (
            "无页概念的文档必须如实返回 None，不得伪造页码"
        )

    def test_search_response_schema_exposes_page_number(self):
        """/knowledge/search 的响应契约也要携带页码（检索字段贯通）。"""
        from app.schemas.knowledge import SearchResult

        with_page = SearchResult(
            id="c1", text="正文", score=0.5, metadata={}, source={}, page_number=9
        )
        without_page = SearchResult(
            id="c2", text="正文", score=0.5, metadata={}, source={}
        )
        assert with_page.page_number == 9
        assert without_page.page_number is None


# ══════════════════════════════════════════════════════════════════════════
# 5 · REST 搜索服务：结果重排时不得丢弃页码
# ══════════════════════════════════════════════════════════════════════════


class TestKnowledgeServicePassthrough:
    """``/knowledge/search`` 链路的中间层：knowledge_service 会重建结果字典。

    端点在服务输出上读 ``r.get("page_number")``；服务层若不在重建字典中搬运
    该字段，端点即使声明了 schema 字段也只会拿到 None（#197 同类失败模式）。
    """

    @pytest.mark.asyncio
    async def test_search_service_formats_page_number(self, monkeypatch):
        import app.services.rag_service as rag_service_mod
        from app.services.knowledge_service import KnowledgeService

        fake = MagicMock()
        fake.search_knowledge_base = AsyncMock(return_value=([{
            "document_id": "doc01",
            "text": "数据安全分级正文",
            "score": 0.5,
            "relevance_score": None,
            "filename": "policy.pdf",
            "snippet": "数据安全分级正文",
            "overlap_score": 0.3,
            "mmr_score": 0.0,
            "page_number": 6,
        }], None))
        monkeypatch.setattr(rag_service_mod, "rag_service", fake)

        out = await KnowledgeService().search_knowledge(
            "数据安全分级", 1, top_k=5
        )
        assert out[0]["page_number"] == 6, (
            "knowledge_service 重建结果时丢弃 page_number —— /search 端点将恒返回 null"
        )
