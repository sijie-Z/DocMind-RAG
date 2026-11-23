"""红线台账 OOXML 解析器单测：全部 DOCX 在内存中合成，不落盘、不联网。"""

from __future__ import annotations

import copy
import hashlib
import io
import zipfile

import pytest

from app.exceptions import ValidationError
from app.models.review import REVIEW_REDLINE_TEXT_PROVENANCE
from app.services.review_redline_service import (
    MAX_REDLINE_ROWS,
    MAX_REDLINE_TEXT_CHARS,
    MAX_REDLINE_UPLOAD_BYTES,
    parse_redline_docx,
    parser_version,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = f'xmlns:w="{W}"'
DOCX_MIME_SENTINEL = b""  # 解析器不依赖文件名/内容类型


def _docx(
    document_xml: str, *, comments_xml: str | None = None, extra: dict[str, bytes] | None = None
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            f'<?xml version="1.0" encoding="UTF-8"?><w:document {NS}><w:body>{document_xml}</w:body></w:document>',
        )
        if comments_xml is not None:
            archive.writestr(
                "word/comments.xml",
                f'<?xml version="1.0" encoding="UTF-8"?><w:comments {NS}>{comments_xml}</w:comments>',
            )
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _assert_only_facts(result):
    forbidden = {"risk", "verdict", "recommendation", "legal_opinion"}
    for value in [result.__dict__, *result.rows]:
        flat = repr(value).lower()
        for key in forbidden:
            assert f"'{key}'" not in flat
    assert all(row["text_provenance"] == REVIEW_REDLINE_TEXT_PROVENANCE for row in result.rows)


def test_insertions_deletions_and_deltext_are_fact_rows():
    document = (
        "<w:p><w:r><w:t>第3条 原文开头</w:t></w:r>"
        '<w:del w:id="1" w:author="对方律师" w:date="2026-01-02T10:00:00Z">'
        "<w:r><w:delText>30天</w:delText></w:r></w:del>"
        '<w:ins w:id="2" w:author="对方律师" w:date="2026-01-02T10:05:00Z">'
        "<w:r><w:t>60天</w:t></w:r></w:ins>"
        "<w:r><w:t> 结尾</w:t></w:r></w:p>"
    )
    result = parse_redline_docx(_docx(document))
    assert result.revision_count == 2
    assert [row["revision_type"] for row in result.rows] == ["deletion", "insertion"]
    deletion, insertion = result.rows
    assert deletion["before_text"] == "30天"
    assert deletion["after_text"] is None
    assert insertion["after_text"] == "60天"
    assert insertion["before_text"] is None
    assert deletion["before_sha256"] == hashlib.sha256("30天".encode()).hexdigest()
    assert insertion["after_sha256"] == hashlib.sha256("60天".encode()).hexdigest()
    assert deletion["author"] == "对方律师"
    assert deletion["authored_at"].isoformat() == "2026-01-02T10:00:00+00:00"
    assert deletion["pair_key"] == insertion["pair_key"]
    assert deletion["clause_no"] == ["3"]
    assert parser_version == "redline-ooxml-v1"
    _assert_only_facts(result)


def test_comments_range_start_end_and_reference_are_deduplicated():
    document = (
        '<w:p><w:commentRangeStart w:id="7"/>'
        "<w:r><w:t>付款条款</w:t></w:r>"
        '<w:commentRangeEnd w:id="7"/>'
        '<w:r><w:commentReference w:id="7"/></w:r></w:p>'
    )
    comments = (
        '<w:comment w:id="7" w:author="李四" w:date="2026-03-04T08:00:00Z">'
        "<w:p><w:r><w:t>请确认期限</w:t></w:r></w:p></w:comment>"
    )
    result = parse_redline_docx(_docx(document, comments_xml=comments))
    assert result.comment_count == 1
    row = result.rows[0]
    assert row["revision_type"] == "comment"
    assert row["comment_id"] == "7"
    assert row["after_text"] == "请确认期限"
    assert row["comment_author"] == "李四"
    assert row["comment_date"].isoformat() == "2026-03-04T08:00:00+00:00"
    assert row["clause_no"] is None
    _assert_only_facts(result)


def test_move_and_format_changes_are_unparsed_with_reason_codes():
    document = (
        '<w:p><w:moveFrom w:id="1" w:author="甲"><w:r><w:delText>旧位置</w:delText></w:r></w:moveFrom>'
        '<w:moveTo w:id="2" w:author="甲"><w:r><w:t>新位置</w:t></w:r></w:moveTo>'
        '<w:pPr><w:pPrChange w:id="3" w:author="甲"><w:pPr><w:spacing w:after="200"/></w:pPr></w:pPrChange></w:pPr>'
        '<w:r><w:rPr><w:rPrChange w:id="4" w:author="甲"><w:rPr><w:b/></w:rPr></w:rPrChange></w:rPr>'
        "<w:t>正文</w:t></w:r></w:p>"
    )
    result = parse_redline_docx(_docx(document))
    assert result.unparsed_count >= 4
    reasons = {
        reason
        for row in result.rows
        if row["revision_type"] == "unparsed"
        for reason in row["unparsed_reason"]
    }
    assert {"move", "pPrChange", "rPrChange"} <= reasons
    assert any(row["before_text"] == "旧位置" for row in result.rows)
    assert result.submission_status == "partial"
    # 无法归类的结构必须有显式原因码；无结构重叠时不必强制 warning。
    _assert_only_facts(result)


def test_nested_revisions_emit_overlap_warnings_and_keep_reasons():
    document = (
        '<w:p><w:customXml><w:ins w:id="1" w:author="甲"><w:r><w:t>嵌套插入</w:t></w:r></w:ins>'
        '<w:customXmlInsRangeStart w:id="2"/></w:customXml></w:p>'
    )
    result = parse_redline_docx(_docx(document))
    assert any(row["after_text"] == "嵌套插入" for row in result.rows)
    assert any(warning.startswith("overlap:") for warning in result.parse_warnings)
    assert any(row["revision_type"] == "unparsed" for row in result.rows)
    _assert_only_facts(result)


def _docx_without_document_part() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/other.xml", f"<w:document {NS}/>")
    return buffer.getvalue()


@pytest.mark.parametrize(
    "builder, message_part",
    [
        (lambda: b"not a zip at all", "ZIP"),
        (_docx_without_document_part, "DOCX"),
        (lambda: _docx("<w:p><w:t>oops</w:t>", extra={}), "XML"),
        (
            lambda: _docx(
                "<w:p><w:t>ok</w:t></w:p>",
                extra={"EncryptionInfo": b"encrypted", "EncryptedPackage": b"payload"},
            ),
            "加密",
        ),
    ],
)
def test_malformed_missing_encrypted_docx_rejected(builder, message_part):
    with pytest.raises(ValidationError) as caught:
        parse_redline_docx(builder())
    assert caught.value.status_code == 400
    assert message_part in str(caught.value.message)


def test_zip_bomb_and_upload_cap_rejected():
    # 单成员超过总预算（使用 store 避免实际压缩 20MB 的耗时）
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("word/document.xml", b"x" * (MAX_REDLINE_UPLOAD_BYTES + 1))
    with pytest.raises(ValidationError):
        parse_redline_docx(bomb.getvalue())
    with pytest.raises(ValidationError):
        parse_redline_docx(b"x" * (MAX_REDLINE_UPLOAD_BYTES + 1))

    # 高压缩比守卫：~400KB 重复文本压缩到几 KB（声明的解压体积仍合法）
    ratio = io.BytesIO()
    huge_text = "第1条 " + ("相" * 360_000)
    with zipfile.ZipFile(ratio, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            f"<w:document {NS}><w:body><w:p><w:r><w:t>{huge_text}</w:t></w:r></w:p></w:body></w:document>",
        )
    with pytest.raises(ValidationError):
        parse_redline_docx(ratio.getvalue())


def test_unsupported_compression_and_corrupt_deflate_are_400():
    # zipfile.write 拒绝写入 method=99，因此先写合法 deflate ZIP，再改头字节。
    unsupported = bytearray(_docx("<w:p><w:r><w:t>ok</w:t></w:r></w:p>"))
    local = unsupported.find(b"PK\x03\x04")
    central = unsupported.find(b"PK\x01\x02")
    for header in (local, central):
        method_offset = 8 if header == local else 10
        unsupported[header + method_offset : header + method_offset + 2] = (99).to_bytes(
            2, "little"
        )
    with pytest.raises(ValidationError) as caught:
        parse_redline_docx(bytes(unsupported))
    assert caught.value.status_code == 400

    # 翻转压缩流前几个字节 → zlib.error；不能穿透成 500。
    valid = bytearray(_docx("<w:p><w:r><w:t>" + "长" * 200 + "</w:t></w:r></w:p>"))
    start = valid.find(b"PK\x03\x04")
    name_len = int.from_bytes(valid[start + 26 : start + 28], "little")
    extra_len = int.from_bytes(valid[start + 28 : start + 30], "little")
    payload_start = start + 30 + name_len + extra_len
    for offset in range(payload_start, payload_start + 8):
        valid[offset] ^= 0xFF
    with pytest.raises(ValidationError) as caught:
        parse_redline_docx(bytes(valid))
    assert caught.value.status_code == 400


def test_streamed_decompressed_bytes_are_capped_not_only_declared_sizes(monkeypatch):
    """声明体积骗过目录预检后，逐块解压计数仍必须给出明确的 400。

    不能只把 ZIP 头的 file_size 改小：zipfile 会按声明值截断读取并抛 Bad CRC，
    那样即使删掉流式上限测试也会通过（假绿）。这里用真正的 DOCX 字节，仅让
    infolist() 返回 file_size 被改小的副本，archive.open() 仍读完整 deflate 流；
    删除流式上限后解析会成功，测试因此转红。
    """
    monkeypatch.setattr("app.services.review_redline_service.MAX_REDLINE_UPLOAD_BYTES", 2000)
    real_body = "长" * 5000
    payload = _docx(f"<w:p><w:r><w:t>{real_body}</w:t></w:r></w:p>")
    assert len(payload) < 2000  # 压缩后足够小，能通过上传前置检查
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.getinfo("word/document.xml").file_size > 2000  # 解压后确实超上限

    real_infolist = zipfile.ZipFile.infolist

    def declared_size_lies(self):
        # 仅伪造目录项的声明体积；真实压缩流与 CRC 保持合法。
        infos = []
        for info in real_infolist(self):
            forged = copy.copy(info)
            forged.file_size = 10
            infos.append(forged)
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", declared_size_lies)
    with pytest.raises(ValidationError) as caught:
        parse_redline_docx(payload)
    assert caught.value.status_code == 400
    assert "DOCX 成员解压后超过上限" in str(caught.value.message)


def test_deeply_nested_xml_yields_unparsed_row_instead_of_recursion_error():
    depth = 300
    inner = '<w:ins w:id="999"><w:r><w:t>深层修订</w:t></w:r></w:ins>'
    document = "".join(f'<w:customXml w:id="{i}">' for i in range(depth))
    document += inner + "</w:customXml>" * depth
    result = parse_redline_docx(_docx(document))
    assert any("xml_depth_cap_exceeded" in warning for warning in result.parse_warnings)
    assert any(
        row["revision_type"] == "unparsed" and "xml_depth_cap" in row["unparsed_reason"]
        for row in result.rows
    )
    assert result.submission_status == "partial"
    _assert_only_facts(result)


def test_hash_stability_for_identical_bytes():
    payload = _docx('<w:p><w:ins w:id="1"><w:r><w:t>稳定文本</w:t></w:r></w:ins></w:p>')
    first = parse_redline_docx(payload, "a.docx")
    second = parse_redline_docx(payload, "completely-different-name.docx")
    assert first == second
    assert first.rows[0]["after_sha256"] == hashlib.sha256("稳定文本".encode()).hexdigest()
    assert DOCX_MIME_SENTINEL == b""


def test_text_cap_truncates_with_warning_and_hashes_truncated_text():
    text = "长" * (MAX_REDLINE_TEXT_CHARS + 25)
    payload = _docx(f'<w:p><w:ins w:id="1"><w:r><w:t>{text}</w:t></w:r></w:ins></w:p>')
    result = parse_redline_docx(payload)
    row = result.rows[0]
    assert len(row["after_text"]) == MAX_REDLINE_TEXT_CHARS
    assert row["after_sha256"] == hashlib.sha256(row["after_text"].encode()).hexdigest()
    assert any("text_cap_exceeded" in warning for warning in result.parse_warnings)
    assert result.submission_status == "partial"


def test_row_cap_emits_partial_with_explicit_warning():
    insertions = "".join(
        f'<w:ins w:id="{i}"><w:r><w:t>第{i}条</w:t></w:r></w:ins>'
        for i in range(MAX_REDLINE_ROWS + 3)
    )
    result = parse_redline_docx(_docx(f"<w:p>{insertions}</w:p>"))
    assert len(result.rows) == MAX_REDLINE_ROWS
    assert result.revision_count == MAX_REDLINE_ROWS
    assert result.submission_status == "partial"
    assert any("row_cap_exceeded" in warning for warning in result.parse_warnings)


def test_range_marker_only_docx_is_never_reported_as_clean():
    """只有范围标记的 DOCX 必须留下 unparsed 行与显式告警，不能 0/0/parsed。"""
    document = (
        '<w:p><w:moveFromRangeStart w:id="1"/><w:moveFromRangeEnd w:id="1"/>'
        '<w:moveToRangeStart w:id="2"/><w:moveToRangeEnd w:id="2"/>'
        '<w:insRangeStart w:id="3"/><w:insRangeEnd w:id="3"/>'
        '<w:delRangeStart w:id="4"/><w:delRangeEnd w:id="4"/></w:p>'
    )
    result = parse_redline_docx(_docx(document))
    assert result.rows
    assert result.unparsed_count == 8
    assert result.submission_status == "partial"
    assert result.parse_warnings
    reasons = {reason for row in result.rows for reason in (row["unparsed_reason"] or [])}
    assert reasons == {"move_range", "revision_range"}
    # 结束标记没有可见文本也必须留档。
    assert all(row["before_text"] is None for row in result.rows)
    _assert_only_facts(result)


def test_nested_opposite_revisions_keep_text_on_correct_side():
    """w:ins 中的 w:del / w:del 中的 w:ins 不能混进对方方向的事实行。"""
    document = (
        '<w:p><w:ins w:id="1" w:author="甲">'
        "<w:r><w:t>新增外层</w:t></w:r>"
        '<w:del w:id="2" w:author="乙"><w:r><w:delText>内层删除</w:delText></w:r></w:del>'
        "</w:ins>"
        '<w:del w:id="3" w:author="甲">'
        "<w:r><w:delText>删除外层</w:delText></w:r>"
        '<w:ins w:id="4" w:author="乙"><w:r><w:t>内层新增</w:t></w:r></w:ins>'
        "</w:del></w:p>"
    )
    result = parse_redline_docx(_docx(document))
    insert = next(row for row in result.rows if row["revision_type"] == "insertion")
    delete = next(row for row in result.rows if row["revision_type"] == "deletion")
    assert insert["after_text"] == "新增外层"
    assert "内层删除" not in (insert["after_text"] or "")
    assert delete["before_text"] == "删除外层"
    assert "内层新增" not in (delete["before_text"] or "")
    overlap_rows = [
        row
        for row in result.rows
        if row["unparsed_reason"] and "nested_opposite_revision" in row["unparsed_reason"]
    ]
    assert len(overlap_rows) == 2
    assert {row["before_text"] for row in overlap_rows} == {"内层删除", "内层新增"}
    assert all(row["revision_type"] == "unparsed" for row in overlap_rows)
    assert any("nested_opposite_revision" in warning for warning in result.parse_warnings)
    assert result.submission_status == "partial"
    _assert_only_facts(result)


def test_comment_body_nested_revisions_keep_direction_and_stable_reasons():
    document = '<w:p><w:r><w:t>正文</w:t></w:r><w:commentRangeStart w:id="1"/></w:p>'
    comments = (
        '<w:comment w:id="1" w:author="李四">'
        "<w:p><w:r><w:t>批注可见文本</w:t></w:r>"
        '<w:ins w:id="2"><w:r><w:t>批注内插入</w:t></w:r></w:ins>'
        '<w:del w:id="3"><w:r><w:delText>批注内删除</w:delText></w:r></w:del>'
        "</w:p></w:comment>"
    )
    result = parse_redline_docx(_docx(document, comments_xml=comments))
    comment = next(row for row in result.rows if row["revision_type"] == "comment")
    assert comment["after_text"] == "批注可见文本"
    nested_rows = [
        row for row in result.rows if row["comment_id"] == "1" and row["unparsed_reason"]
    ]
    assert len(nested_rows) == 2  # 每处嵌套修订一行，父/子不双计
    assert [(row["revision_type"], row["unparsed_reason"]) for row in nested_rows] == [
        ("insertion", ["comment_insertion"]),
        ("deletion", ["comment_deletion"]),
    ]
    assert nested_rows[0]["after_text"] == "批注内插入"
    assert nested_rows[1]["before_text"] == "批注内删除"
    assert all(row["comment_id"] == "1" for row in nested_rows)
    assert all(row["location"]["source"] == "word/comments.xml" for row in nested_rows)
    assert sum("comment_nested_revision" in w for w in result.parse_warnings) == 2
    _assert_only_facts(result)


def test_field_result_revision_is_unparsed_not_counted_as_insertion():
    document = (
        '<w:p><w:ins w:id="1" w:author="甲">'
        '<w:fldSimple w:instr="PAGE"><w:r><w:t>字段结果</w:t></w:r></w:fldSimple>'
        "</w:ins></w:p>"
    )
    result = parse_redline_docx(_docx(document))
    insertion = next(row for row in result.rows if row["revision_type"] == "insertion")
    assert insertion["after_text"] is None
    unparsed = [
        row
        for row in result.rows
        if row["unparsed_reason"] and "field_result_revision" in row["unparsed_reason"]
    ]
    assert len(unparsed) == 1
    assert any("field_result_revision" in warning for warning in result.parse_warnings)
    _assert_only_facts(result)


def test_author_and_comment_id_are_bounded_to_orm_column_widths():
    document = (
        f'<w:p><w:ins w:id="1" w:author="{"甲" * 300}"><w:r><w:t>新增</w:t></w:r></w:ins>'
        '<w:commentRangeStart w:id="' + ("9" * 80) + '"/></w:p>'
    )
    comment_id = "9" * 80
    comments = (
        f'<w:comment w:id="{comment_id}" w:author="{"乙" * 300}">'
        "<w:p><w:r><w:t>批注</w:t></w:r></w:p></w:comment>"
    )
    result = parse_redline_docx(_docx(document, comments_xml=comments))
    for row in result.rows:
        assert row["author"] is None or len(row["author"]) <= 200
        assert row["comment_author"] is None or len(row["comment_author"]) <= 200
        assert row["comment_id"] is None or len(row["comment_id"]) <= 64
    _assert_only_facts(result)


def test_complex_field_form_excludes_field_result_from_insertion():
    """标准 fldChar 复杂字段的 w:t 结果只进 unparsed 行，不进 insertion。"""
    document = (
        "<w:p><w:ins w:id='1' w:author='X'>"
        "<w:r><w:fldChar w:fldCharType='begin'/></w:r>"
        "<w:r><w:instrText> SEQ 表 </w:instrText></w:r>"
        "<w:r><w:fldChar w:fldCharType='separate'/></w:r>"
        "<w:r><w:t>7</w:t></w:r>"
        "<w:r><w:fldChar w:fldCharType='end'/></w:r>"
        "</w:ins></w:p>"
    )
    result = parse_redline_docx(_docx(document))
    insertion = next(row for row in result.rows if row["revision_type"] == "insertion")
    assert insertion["after_text"] is None
    field_rows = [
        row
        for row in result.rows
        if row["unparsed_reason"] and "field_result_revision" in row["unparsed_reason"]
    ]
    assert len(field_rows) == 1
    assert field_rows[0]["before_text"] == "7"
    assert field_rows[0]["location"]["field_result"] is True
    _assert_only_facts(result)


def test_field_text_inside_nested_opposite_revision_is_attributed_once():
    document = (
        "<w:p><w:ins w:id='1' w:author='X'>A"
        "<w:del w:id='2' w:author='Y'>B"
        "<w:r><w:fldSimple w:instr='PAGE'><w:r><w:t>9</w:t></w:r></w:fldSimple></w:r>"
        "</w:del></w:ins></w:p>"
    )
    result = parse_redline_docx(_docx(document))
    insertion = next(row for row in result.rows if row["revision_type"] == "insertion")
    assert insertion["after_text"] == "A"
    nested = [
        row
        for row in result.rows
        if row["unparsed_reason"] and "nested_opposite_revision" in row["unparsed_reason"]
    ]
    fields = [
        row
        for row in result.rows
        if row["unparsed_reason"] and "field_result_revision" in row["unparsed_reason"]
    ]
    assert len(nested) == len(fields) == 1
    assert nested[0]["before_text"] == "B"
    assert fields[0]["before_text"] == "9"
    assert "9" not in (nested[0]["before_text"] or "")
    _assert_only_facts(result)


def test_nested_revisions_inside_comment_keep_direction_and_text():
    document = '<w:p><w:commentRangeStart w:id="1"/><w:r><w:t>x</w:t></w:r></w:p>'
    comments = (
        '<w:comment w:id="1" w:author="X">'
        "<w:p><w:ins w:id='2' w:author='X'>outer-ins"
        "<w:del w:id='3' w:author='Y'>inner-del</w:del>"
        "</w:ins></w:p></w:comment>"
    )
    result = parse_redline_docx(_docx(document, comments_xml=comments))
    assert not any(w.startswith("empty_comment_body:") for w in result.parse_warnings)
    nested = [row for row in result.rows if row["comment_id"] == "1" and row["unparsed_reason"]]
    assert len(nested) == 2
    reasons = {(row["revision_type"], tuple(row["unparsed_reason"] or [])) for row in nested}
    assert reasons == {
        ("insertion", ("comment_insertion",)),
        ("deletion", ("comment_deletion",)),
    }
    inserted = next(row for row in nested if row["revision_type"] == "insertion")
    deleted = next(row for row in nested if row["revision_type"] == "deletion")
    assert inserted["after_text"] == "outer-ins"
    assert deleted["before_text"] == "inner-del"
    assert all(row["location"]["source"] == "word/comments.xml" for row in nested)
    _assert_only_facts(result)


def test_nested_same_side_insertion_is_not_double_counted():
    document = (
        "<w:p><w:ins w:id='1' w:author='X'><w:r><w:t>A</w:t></w:r>"
        "<w:ins w:id='2' w:author='X'><w:r><w:t>B</w:t></w:r></w:ins></w:ins></w:p>"
    )
    result = parse_redline_docx(_docx(document))
    assert result.revision_count == 1
    insertions = [row for row in result.rows if row["revision_type"] == "insertion"]
    assert len(insertions) == 1
    assert insertions[0]["after_text"] == "AB"
    _assert_only_facts(result)


def test_empty_comment_body_emits_explicit_warning():
    document = '<w:p><w:commentRangeStart w:id="5"/><w:commentRangeEnd w:id="5"/></w:p>'
    comments = '<w:comment w:id="5" w:author="李四"><w:p/></w:comment>'
    result = parse_redline_docx(_docx(document, comments_xml=comments))
    assert result.comment_count == 1
    assert any(w.startswith("empty_comment_body:") for w in result.parse_warnings)
    assert result.submission_status == "partial"


def test_plain_document_without_revisions_is_parsed_and_empty():
    result = parse_redline_docx(_docx("<w:p><w:r><w:t>无修订</w:t></w:r></w:p>"))
    assert result.rows == []
    assert result.submission_status == "parsed"
    assert result.parse_warnings == []
    assert result.revision_count == result.comment_count == result.unparsed_count == 0
    _assert_only_facts(result)
