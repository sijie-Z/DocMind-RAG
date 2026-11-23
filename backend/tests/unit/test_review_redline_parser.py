"""红线台账 OOXML 解析器单测：全部 DOCX 在内存中合成，不落盘、不联网。"""

from __future__ import annotations

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
    monkeypatch.setattr("app.services.review_redline_service.MAX_REDLINE_UPLOAD_BYTES", 2000)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            f"<w:document {NS}><w:body><w:p><w:r><w:t>{'长' * 5000}</w:t></w:r></w:p></w:body></w:document>",
        )
    payload = bytearray(buffer.getvalue())
    assert len(payload) < 2000  # 压缩后足够小，能通过上传前置检查

    # 伪造目录项：声明解压后只有 10 字节（骗过 file_size 预检），实际流远大于上限。
    local = payload.find(b"PK\x03\x04")
    central = payload.find(b"PK\x01\x02")
    for header, size_offset in ((local, 22), (central, 24)):
        payload[header + size_offset : header + size_offset + 4] = (10).to_bytes(4, "little")
    with pytest.raises(ValidationError) as caught:
        parse_redline_docx(bytes(payload))
    assert caught.value.status_code == 400


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


def test_plain_document_without_revisions_is_parsed_and_empty():
    result = parse_redline_docx(_docx("<w:p><w:r><w:t>无修订</w:t></w:r></w:p>"))
    assert result.rows == []
    assert result.submission_status == "parsed"
    assert result.parse_warnings == []
    assert result.revision_count == result.comment_count == result.unparsed_count == 0
    _assert_only_facts(result)
