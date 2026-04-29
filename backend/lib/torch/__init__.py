"""
最小化PyTorch兼容层 - 用于在没有PyTorch的情况下运行基础功能
"""

import math
import random
from typing import List, Union, Optional

class Tensor:
    """最小化张量实现"""
    
    def __init__(self, data, dtype=None):
        if isinstance(data, Tensor):
            self.data = data.data
        elif isinstance(data, (list, tuple)):
            self.data = self._flatten_list(data)
        else:
            self.data = [data] if not isinstance(data, list) else data
        self.dtype = dtype or float
        self.shape = self._compute_shape(data)
    
    def _flatten_list(self, lst):
        """展平嵌套列表"""
        result = []
        for item in lst:
            if isinstance(item, (list, tuple)):
                result.extend(self._flatten_list(item))
            else:
                result.append(float(item))
        return result
    
    def _compute_shape(self, data):
        """计算形状"""
        if not isinstance(data, list):
            return ()
        shape = [len(data)]
        if data and isinstance(data[0], list):
            shape.extend(self._compute_shape(data[0]))
        return tuple(shape)
    
    def __repr__(self):
        return f"tensor({self.data})"
    
    def __len__(self):
        return len(self.data)
    
    def __getitem__(self, idx):
        return self.data[idx]
    
    def numpy(self):
        """转换为numpy数组（简化版）"""
        return self.data

def tensor(data, dtype=None):
    """创建张量"""
    return Tensor(data, dtype)

def rand(*shape):
    """创建随机张量"""
    size = 1
    for dim in shape:
        size *= dim
    data = [random.random() for _ in range(size)]
    return Tensor(data)

def cosine_similarity(a: Tensor, b: Tensor) -> float:
    """计算余弦相似度"""
    if len(a) != len(b):
        raise ValueError("张量维度不匹配")
    
    dot_product = sum(x * y for x, y in zip(a.data, b.data))
    norm_a = math.sqrt(sum(x * x for x in a.data))
    norm_b = math.sqrt(sum(x * x for x in b.data))
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return dot_product / (norm_a * norm_b)

def cuda_is_available():
    """检查CUDA是否可用"""
    return False

# 版本信息
__version__ = "0.1.0-minimal"
