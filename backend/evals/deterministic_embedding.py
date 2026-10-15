"""CI 模式的确定性伪嵌入（#208 第⑤步；纯标准库，无网络）。

CI 环境没有 Ollama / 真实嵌入服务。评估 harness 的 ``RetrieveFn`` 协议注入
只解决了编排层——``HybridRetriever`` 的向量臂本身要调用 embedding 客户端。
本模块提供一个与 OpenAI 客户端**鸭子类型兼容**的确定性替身：

- ``DeterministicEmbeddingClient().embeddings.create(input=..., model=...)``
  → ``resp.data[i].embedding``（与真实客户端同形，单条 / 批量输入均可）；
- 向量算法 = 非空白字符 unigram + 相邻 bigram 的 hashing trick：
  每个 token 经 sha256 映到固定桶（dim=256）累加计数，再做 L2 归一化。
  语义上等价于「字符 n-gram 余弦相似度」——共享字形的文本确实更相似，
  所以 CI 模式的向量臂仍在做**有意义**的工作（可解释），同时逐位可复现
  （算法、桶宽、哈希全部写死，固定输入 → 固定输出，跨平台 bit 级一致）。

协议锁定：改变本文件的任何数值行为 = 改变 CI 门禁的基线语义——必须同步、
有意识地更新 ``baselines/ci_baseline.json``（显式 PR 行为）。``CI_EMBEDDING_ID``
是写进基线与报告的可追溯标识。

不用「随机向量 / 哈希直出」的原因：那样的向量臂排序等价于噪声，门禁只能
保护「管道不崩」；hashing trick 让相似文本真的靠前，门禁保护的是一个**行为面**。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

CI_EMBEDDING_ID = "hash-bigram-char-v1"
CI_EMBEDDING_DIM = 256
_HASH_BYTES = 4


def _tokens(text: str) -> list[str]:
    """非空白字符的 unigram + 相邻 bigram（保序，含重复计数）。"""
    chars = [c for c in text if not c.isspace()]
    bigrams = [a + b for a, b in zip(chars, chars[1:], strict=False)]
    return chars + bigrams


def _bucket(token: str, dim: int) -> int:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:_HASH_BYTES], "big") % dim


def embed_text(text: str, dim: int = CI_EMBEDDING_DIM) -> list[float]:
    """确定性伪嵌入：token 分桶计数 → L2 归一化（空文本 → 全零向量）。"""
    if dim < 1:
        raise ValueError("dim 必须 ≥1")
    vec = [0.0] * dim
    for token in _tokens(text):
        vec[_bucket(token, dim)] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


@dataclass(frozen=True)
class _EmbeddingRecord:
    """与 OpenAI 客户端 ``resp.data[i]`` 的消费面一致（index / embedding）。"""

    index: int
    embedding: list[float]
    object: str = "embedding"


@dataclass(frozen=True)
class _EmbeddingResponse:
    data: list[_EmbeddingRecord]
    model: str = CI_EMBEDDING_ID
    object: str = "list"


class _EmbeddingsAPI:
    def __init__(self, dim: int) -> None:
        self._dim = dim

    async def create(self, input, model: str | None = None, **kwargs) -> _EmbeddingResponse:
        texts = [input] if isinstance(input, str) else list(input)
        return _EmbeddingResponse(
            data=[
                _EmbeddingRecord(index=i, embedding=embed_text(t, self._dim))
                for i, t in enumerate(texts)
            ],
            model=model or CI_EMBEDDING_ID,
        )


class DeterministicEmbeddingClient:
    """伪嵌入客户端：只实现 runner / retriever 消费的两个面（embeddings.create / close）。"""

    def __init__(self, dim: int = CI_EMBEDDING_DIM) -> None:
        self.dim = dim
        self.embeddings = _EmbeddingsAPI(dim)

    async def close(self) -> None:
        """与 AsyncOpenAI.close() 同形的空实现（runner 统一关停路径）。"""
