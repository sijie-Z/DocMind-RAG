"""Original-text provenance primitives shared by retrieval and Chat.

This module deliberately has no Elasticsearch, database, or model imports. A
missing/unknown provenance marker is always legacy, never evidence of originality.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

PARSER_ORIGINAL = "parser_original"
LEGACY_UNVERIFIED = "legacy_unverified"
COMPAT = "compat"
STRICT = "strict"
CACHE_SCHEMA_VERSION = "v3"


def normalize_mode(mode: str | None) -> str:
    """Return a validated provenance mode; unset defaults to rolling compatibility."""
    if mode is None:
        return COMPAT
    normalized = str(mode).strip().lower()
    if normalized not in (COMPAT, STRICT):
        raise ValueError(f"未知 RAG_SOURCE_PROVENANCE_MODE: {mode!r}")
    return normalized


def provenance_of(item: dict[str, Any]) -> str:
    """Read snake/camel provenance markers; missing markers mean legacy unverified."""
    marker = item.get("text_provenance") or item.get("textProvenance")
    return PARSER_ORIGINAL if marker == PARSER_ORIGINAL else LEGACY_UNVERIFIED


def original_text_of(item: dict[str, Any]) -> str | None:
    """Return a non-empty original only when its producer also attests its provenance."""
    if provenance_of(item) != PARSER_ORIGINAL:
        return None
    original = item.get("original_text")
    if isinstance(original, str) and original.strip():
        return original
    return None


def is_original_result(item: dict[str, Any]) -> bool:
    return original_text_of(item) is not None


def sources_have_unverified(sources: list[dict[str, Any]]) -> bool:
    """Source cards need only their attested marker; never infer it from content."""
    return any(provenance_of(source) != PARSER_ORIGINAL for source in sources)


def normalize_source_metadata(source: dict[str, Any]) -> dict[str, Any]:
    """Preserve source metadata while making missing legacy markers explicit."""
    normalized = dict(source)
    normalized["textProvenance"] = provenance_of(source)
    if "chunkId" not in normalized and source.get("chunk_id") is not None:
        normalized["chunkId"] = source.get("chunk_id")
    return normalized


class SourceProvenanceUnavailableError(RuntimeError):
    """Strict mode found retrieval hits but none has verifiable original text."""

    code = "source_original_unavailable"
    status = "needs_rebuild"

    def __init__(self, document_ids: list[str], *, unverified_count: int):
        self.document_ids = sorted(set(document_ids))
        self.unverified_count = unverified_count
        super().__init__(
            f"知识库原文尚未完成迁移：{unverified_count} 个检索来源需要重建。"
            "strict 模式不会把检索增强文本当作原文；请先完成原文迁移/重建。"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "document_ids": self.document_ids,
            "unverified_count": self.unverified_count,
        }


def normalize_result_for_evidence(
    item: dict[str, Any],
    *,
    query: str,
    mode: str | None,
    snippet_builder: Callable[[str, str], str],
) -> dict[str, Any]:
    """Rebuild prompt/snippet from real O; never trust precomputed evidence fields."""
    normalized = dict(item)
    normalized_mode = normalize_mode(mode)
    original = original_text_of(normalized)
    if original is not None:
        normalized["original_text"] = original
        normalized["text_provenance"] = PARSER_ORIGINAL
        normalized["prompt_text"] = original
        normalized["snippet"] = snippet_builder(original, query)
        return normalized

    normalized["original_text"] = None
    normalized["text_provenance"] = LEGACY_UNVERIFIED
    if normalized_mode == STRICT:
        normalized["prompt_text"] = ""
        normalized["snippet"] = ""
        return normalized

    # Only explicit legacy compat may use the old retrieval-text fallback. Dual-field
    # entries always take the O branch above, even when prompt_text/snippet contain E.
    retrieval_text = (
        normalized.get("prompt_text")
        or normalized.get("retrieval_text")
        or normalized.get("text")
        or normalized.get("content")
        or normalized.get("snippet")
        or ""
    )
    normalized["prompt_text"] = retrieval_text
    if not normalized.get("snippet"):
        normalized["snippet"] = snippet_builder(retrieval_text, query) if retrieval_text else ""
    return normalized


def filter_original_results(
    results: list[dict[str, Any]],
    *,
    mode: str | None,
    query: str,
    snippet_builder: Callable[[str, str], str],
) -> list[dict[str, Any]]:
    """Normalize fresh/cache results, then apply the strict evidence gate.

    Compat preserves every result but still rebuilds dual-field evidence from O.
    Strict keeps verifiable originals; if hits exist but none is original, it raises
    a structured needs-rebuild state instead of returning a misleading empty list.
    """
    normalized = [
        normalize_result_for_evidence(
            item,
            query=query,
            mode=mode,
            snippet_builder=snippet_builder,
        )
        for item in results
    ]
    if normalize_mode(mode) == COMPAT:
        return normalized
    originals = [item for item in normalized if is_original_result(item)]
    if normalized and not originals:
        document_ids = [
            str(item.get("document_id") or item.get("documentId") or "") for item in normalized
        ]
        raise SourceProvenanceUnavailableError(
            [document_id for document_id in document_ids if document_id],
            unverified_count=len(normalized),
        )
    return originals
