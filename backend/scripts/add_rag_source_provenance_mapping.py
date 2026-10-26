"""Add RAG original-text provenance fields to an existing ES index.

Only additive ``indices.put_mapping`` plus read-back verification is performed.
This script never creates, deletes, recreates, or reindexes an index/document.

Usage (after DB migration 018, before deploying the new writer)::

    python scripts/add_rag_source_provenance_mapping.py
    python scripts/add_rag_source_provenance_mapping.py --index docmind_knowledge
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from elasticsearch import AsyncElasticsearch  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.elasticsearch import (  # noqa: E402
    IndexConfigError,
    _source_provenance_mapping_properties,
)

logger = logging.getLogger("add_rag_source_provenance_mapping")


def _properties(mapping: dict, index_name: str) -> dict:
    return mapping.get(index_name, {}).get("mappings", {}).get("properties", {})


def _assert_compatible(field: str, actual: dict, expected: dict) -> None:
    mismatches = {
        key: {"expected": value, "actual": actual.get(key)}
        for key, value in expected.items()
        if actual.get(key) != value
    }
    if mismatches:
        raise IndexConfigError(
            f"字段 {field!r} 已存在但 mapping 不兼容：{mismatches}。"
            "不覆盖既有 mapping；请人工核对后另行迁移。"
        )


async def add_source_provenance_mapping(client, index_name: str) -> list[str]:
    """Add missing provenance fields and verify them; return the added field names."""
    exists = await client.indices.exists(index=index_name)
    if not exists:
        raise IndexConfigError(
            f"索引 {index_name!r} 不存在；本脚本不会创建索引，请先走既有建索引路径。"
        )

    expected = _source_provenance_mapping_properties()
    current_mapping = await client.indices.get_mapping(index=index_name)
    current = _properties(current_mapping, index_name)
    additions = {}
    for field, wanted in expected.items():
        if field in current:
            _assert_compatible(field, current[field], wanted)
        else:
            additions[field] = wanted

    if additions:
        response = await client.indices.put_mapping(index=index_name, properties=additions)
        if not response or response.get("acknowledged") is not True:
            raise RuntimeError(f"additive put_mapping 未获确认：{response!r}")

    # 无论本次是否新增，都以读回结果作为完成判据；不依据调用方假设。
    readback = await client.indices.get_mapping(index=index_name)
    persisted = _properties(readback, index_name)
    for field, wanted in expected.items():
        if field not in persisted:
            raise RuntimeError(f"mapping 读回仍缺字段 {field!r}")
        _assert_compatible(field, persisted[field], wanted)
    return sorted(additions)


async def _amain() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default=settings.ELASTICSEARCH_INDEX_NAME)
    args = parser.parse_args()

    client = AsyncElasticsearch(
        hosts=settings.ELASTICSEARCH_HOSTS,
        verify_certs=getattr(settings, "ELASTICSEARCH_VERIFY_CERTS", False),
        ssl_show_warn=False,
        request_timeout=30,
    )
    try:
        added = await add_source_provenance_mapping(client, args.index)
    except Exception as exc:
        logger.error("原文溯源 mapping 迁移失败：%s", exc)
        return 1
    finally:
        await client.close()

    if added:
        print(f"索引 {args.index} 已新增字段并读回确认：{', '.join(added)}")
    else:
        print(f"索引 {args.index} 的原文溯源字段已存在且兼容，无需变更")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
