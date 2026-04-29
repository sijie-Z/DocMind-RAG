import json
from dataclasses import dataclass, asdict
from typing import List, Dict, Any


@dataclass
class IndexedChunk:
    """
    功能：表示一个已入库的“文本块”，包含原文、向量和简单元信息。
    字段说明（小白版）：
    - id：这个块的唯一标识（字符串）。
    - text：块的原始文本内容。
    - vector：文本的向量表示（用列表保存，方便落盘到 JSON）。
    - meta：额外信息，比如来源文件名、组织标签、是否公开等（演示为可选）。
    """
    id: str
    text: str
    vector: List[float]
    meta: Dict[str, Any] | None = None


def save_index(chunks: List[IndexedChunk], path: str) -> None:
    """
    把整个检索索引保存到本地 JSON 文件。
    小白解释：就像把通讯录写到一个文件里，方便下次直接打开用。
    """
    serializable = [asdict(c) for c in chunks]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)


def load_index(path: str) -> List[IndexedChunk]:
    """
    从本地 JSON 文件读取索引，恢复成对象列表。
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    chunks: List[IndexedChunk] = []
    for item in data:
        chunks.append(IndexedChunk(
            id=item["id"],
            text=item["text"],
            vector=list(map(float, item["vector"])),
            meta=item.get("meta") or None,
        ))
    return chunks