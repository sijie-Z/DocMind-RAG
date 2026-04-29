import re
from typing import List, Tuple, Union

try:
    import torch
except Exception:
    torch = None  # 允许无 PyTorch 环境，也能运行


class HashedVectorizer:
    """
    功能：把文本变成固定维度的“语义向量”（超轻量版本）。
    原理（小白版）：
    - 把文本切成词；
    - 用哈希把每个词映射到 0~dim-1 的某个位置；
    - 在对应位置做计数/加权，得到一个长度为 dim 的向量；
    这不是学术最优，但足够演示 RAG 管线。后续可以替换为高质量 Embedding 模型。
    """

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def _tokenize(self, text: str) -> List[str]:
        # 简单分词：按非字母数字切分，全部小写
        tokens = re.split(r"[^a-zA-Z0-9]+", text.lower())
        return [t for t in tokens if t]

    def transform(self, text: str) -> Union[List[float], "torch.Tensor"]:
        """
        把一段文本转换为向量。优先用 PyTorch 张量，没装 torch 就返回 Python 列表。
        """
        vec = [0.0] * self.dim
        for tok in self._tokenize(text):
            idx = (hash(tok) % self.dim)
            vec[idx] += 1.0

        if torch is not None:
            # 转成 torch 张量，方便后续替换成真正的模型
            return torch.tensor(vec, dtype=torch.float32)
        return vec


def cosine_similarity(vec_a: Union[List[float], "torch.Tensor"],
                      vec_b: Union[List[float], "torch.Tensor"]) -> float:
    """
    计算两个向量的余弦相似度（值越接近 1，表示越相似）。
    小白解释：把两个“方向”比一比，看是否在同一个方向上，越同向越像。
    """
    if torch is not None and isinstance(vec_a, torch.Tensor) and isinstance(vec_b, torch.Tensor):
        if vec_a.dim() == 1:
            vec_a = vec_a.unsqueeze(0)
        if vec_b.dim() == 1:
            vec_b = vec_b.unsqueeze(0)
        a_norm = torch.nn.functional.normalize(vec_a, dim=1)
        b_norm = torch.nn.functional.normalize(vec_b, dim=1)
        return float((a_norm @ b_norm.T)[0, 0].item())

    # 纯 Python 实现
    dot = 0.0
    na = 0.0
    nb = 0.0
    for a, b in zip(vec_a, vec_b):
        dot += a * b
        na += a * a
        nb += b * b
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na ** 0.5 * nb ** 0.5)