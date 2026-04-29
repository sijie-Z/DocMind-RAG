"""
FAISS向量存储 - 高性能向量索引
专门为大厂级RAG系统设计，支持百万级向量检索
"""

import os
import json
import pickle
from typing import List, Dict, Any, Optional, Tuple, Union
from dataclasses import dataclass
from pathlib import Path
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import logging

# 尝试导入numpy，如果失败则创建模拟实现
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    # 创建numpy的模拟实现
    class MockNumpyArray:
        def __init__(self, data, dtype=None):
            if isinstance(data, (list, tuple)):
                self.data = list(data)
            else:
                self.data = data
            self.dtype = dtype or float
            self.shape = self._get_shape()
            self.ndim = len(self.shape)
        
        def _get_shape(self):
            if isinstance(self.data, list) and self.data:
                if isinstance(self.data[0], list):
                    return (len(self.data), len(self.data[0]))
                return (len(self.data),)
            return (0,)
        
        def astype(self, dtype):
            return MockNumpyArray(self.data, dtype)
        
        def copy(self):
            import copy
            return MockNumpyArray(copy.deepcopy(self.data), self.dtype)
        
        def reshape(self, *args):
            # 简化版 reshape (1, -1)
            if len(args) == 2 and args[0] == 1 and args[1] == -1:
                if self.ndim == 1:
                    return MockNumpyArray([self.data], self.dtype)
            return self
            
        def __len__(self):
            return len(self.data) if isinstance(self.data, list) else 0
        
        def __getitem__(self, key):
            if isinstance(self.data, list):
                if isinstance(key, MockNumpyArray):
                    return MockNumpyArray([self.data[i] for i in key.data])
                if hasattr(key, 'data'):
                    return MockNumpyArray([self.data[i] for i in key.data])
                if isinstance(key, slice):
                    return MockNumpyArray(self.data[key])
                if isinstance(key, (list, tuple)):
                    return MockNumpyArray([self.data[i] for i in key])
                return self.data[key]
            return self.data
        
        def __setitem__(self, key, value):
            if isinstance(self.data, list):
                if hasattr(value, 'data'):
                    self.data[key] = value.data
                else:
                    self.data[key] = value

        def __truediv__(self, other):
            if isinstance(other, MockNumpyArray):
                if self.ndim == 2 and other.ndim == 2:
                    return MockNumpyArray([[v / (other.data[i][0] if other.data[i][0] else 1e-8) for v in row] for i, row in enumerate(self.data)])
                elif self.ndim == 1 and other.ndim == 1:
                    return MockNumpyArray([x / (y if y else 1e-8) for x, y in zip(self.data, other.data)])
            elif isinstance(other, (int, float)):
                if self.ndim == 2:
                    return MockNumpyArray([[v / (other if other else 1e-8) for v in row] for row in self.data])
                return MockNumpyArray([x / (other if other else 1e-8) for x in self.data])
            return self

        def __add__(self, other):
            if isinstance(other, (int, float)):
                if self.ndim == 1:
                    return MockNumpyArray([x + other for x in self.data])
            return self
            
        def __mul__(self, other):
            if isinstance(other, MockNumpyArray):
                if self.ndim == 1 and other.ndim == 1:
                    return MockNumpyArray([x * y for x, y in zip(self.data, other.data)])
            elif isinstance(other, (int, float)):
                if self.ndim == 1:
                    return MockNumpyArray([x * other for x in self.data])
            return self

        def any(self):
            return True

    class MockNumpy:
        def __init__(self):
            self.random = self.Random()
            self.linalg = self.Linalg()
            self.float32 = "float32"
            self.ndarray = MockNumpyArray
        
        def array(self, data, dtype=None):
            return MockNumpyArray(data, dtype)
        
        class Random:
            def randn(self, *shape):
                import random
                if len(shape) == 1:
                    return MockNumpyArray([random.gauss(0, 1) for _ in range(shape[0])])
                elif len(shape) == 2:
                    return MockNumpyArray([[random.gauss(0, 1) for _ in range(shape[1])] for _ in range(shape[0])])
                return MockNumpyArray([])
            
            def seed(self, seed):
                import random
                random.seed(seed)
        
        def dot(self, a, b):
            if hasattr(a, 'data') and hasattr(b, 'data'):
                if a.ndim == 2 and b.ndim == 1:
                    return MockNumpyArray([sum(x * y for x, y in zip(row, b.data)) for row in a.data])
                if a.ndim == 1 and b.ndim == 1:
                    return sum(x * y for x, y in zip(a.data, b.data))
            return 0
            
        def where(self, condition, x, y):
            return x if condition.any() else y

        def full(self, shape, fill_value, dtype=None):
            if isinstance(shape, tuple) and len(shape) == 2:
                 return MockNumpyArray([[fill_value for _ in range(shape[1])] for _ in range(shape[0])], dtype)
            return MockNumpyArray([fill_value], dtype)

        class Linalg:
            def norm(self, arr, axis=None, keepdims=False) -> Any:
                if hasattr(arr, 'data'):
                    if arr.ndim == 2:
                        if axis == 1:
                            res = [sum(x*x for x in row)**0.5 for row in arr.data]
                            if keepdims:
                                return MockNumpyArray([[v] for v in res])
                            return MockNumpyArray(res)
                        return sum(x*x for row in arr.data for x in row)**0.5
                    elif arr.ndim == 1:
                        res = sum(x*x for x in arr.data)**0.5
                        if keepdims:
                            return MockNumpyArray([res])
                        return res
                return 1.0
        
        def zeros(self, shape, dtype=None):
            if isinstance(shape, tuple):
                if len(shape) == 2:
                    return MockNumpyArray([[0.0 for _ in range(shape[1])] for _ in range(shape[0])], dtype)
                elif len(shape) == 1:
                    return MockNumpyArray([0.0 for _ in range(shape[0])], dtype)
            return MockNumpyArray([0.0], dtype)
        
        def argsort(self, arr):
            if hasattr(arr, 'data'):
                indexed = [(val, i) for i, val in enumerate(arr.data)]
                indexed.sort(key=lambda x: x[0])  # argsort应该是升序排列
                return MockNumpyArray([i for _, i in indexed])
            return MockNumpyArray([])
    
    np = MockNumpy()  # type: ignore

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    import faiss
    FAISS_AVAILABLE = True
    logger.info("✅ FAISS 库加载成功")
except ImportError:
    faiss = None
    FAISS_AVAILABLE = False
    logger.warning("⚠️ FAISS 库未安装，使用备用实现")

try:
    import torch
    import torch.nn.functional as F
    TORCH_AVAILABLE = True
    logger.info("✅ PyTorch 库加载成功")
except ImportError:
    torch = None
    TORCH_AVAILABLE = False
    logger.warning("⚠️ PyTorch 库未安装，使用备用实现")

@dataclass
class VectorConfig:
    """向量存储配置"""
    dimension: int = 768
    index_type: str = "IVF"  # IVF, HNSW, FLAT
    nlist: int = 1000       # IVF聚类中心数
    nprobe: int = 10        # IVF搜索聚类数
    max_elements: int = 1000000  # 最大向量数
    ef_construction: int = 200   # HNSW构建参数
    ef_search: int = 50         # HNSW搜索参数
    use_gpu: bool = False       # 是否使用GPU
    
class FAISSVectorStore:
    """
    FAISS向量存储管理器
    支持多种索引类型：IVF、HNSW、FLAT
    """
    
    def __init__(self, config: VectorConfig = None, storage_path: str = "./vector_store"):
        self.config = config or VectorConfig()
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(exist_ok=True)
        
        self.index_path = self.storage_path / "faiss.index"
        self.metadata_path = self.storage_path / "metadata.pkl"
        self.mapping_path = self.storage_path / "id_mapping.json"
        
        self.index: Any = None
        self.metadata: Dict[str, Any] = {}
        self.id_mapping: Dict[str, str] = {}
        self.reverse_mapping: Dict[str, str] = {}
        self.next_id = 0
        
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=4)
        
        self._init_index()
        self._load_data()
        
    def _init_index(self):
        """初始化FAISS索引"""
        if not (FAISS_AVAILABLE and faiss):
            logger.warning("FAISS不可用，创建模拟索引")
            self.index = MockFAISSIndex(self.config.dimension)
            return
            
        try:
            dimension = self.config.dimension
            
            if self.config.index_type == "IVF":
                quantizer = faiss.IndexFlatIP(dimension)
                self.index = faiss.IndexIVFFlat(quantizer, dimension, self.config.nlist)
                logger.info(f"创建IVF索引，维度:{dimension}, 聚类数:{self.config.nlist}")
                
            elif self.config.index_type == "HNSW":
                self.index = faiss.IndexHNSWFlat(dimension, 32)
                self.index.hnsw.efConstruction = self.config.ef_construction
                self.index.hnsw.efSearch = self.config.ef_search
                logger.info(f"创建HNSW索引，维度:{dimension}")
                
            else:
                self.index = faiss.IndexFlatIP(dimension)
                logger.info(f"创建FLAT索引，维度:{dimension}")
                
            # IVF类型索引才支持 nprobe
            if hasattr(self.index, 'nprobe'):
                setattr(self.index, 'nprobe', self.config.nprobe)
                
            # GPU支持 (使用 Any 绕过动态属性检查)
            f_any: Any = faiss
            if self.config.use_gpu and f_any.get_num_gpus() > 0:
                self.index = f_any.index_cpu_to_gpu(f_any.StandardGpuResources(), 0, self.index)
                logger.info("✅ 启用GPU加速")
                
        except Exception as e:
            logger.error(f"FAISS索引创建失败: {e}")
            self.index = MockFAISSIndex(self.config.dimension)
    
    def add_vectors(self, vectors: Any, ids: List[str], metadata: List[Dict[str, Any]] = None):
        """添加向量到索引"""
        if len(vectors) == 0:
            return
            
        with self.lock:
            try:
                # 统一转为 float32
                if not isinstance(vectors, np.ndarray):
                    vectors = np.array(vectors, dtype=getattr(np, 'float32', None))
                elif vectors.dtype != getattr(np, 'float32', None):
                    vectors = vectors.astype(getattr(np, 'float32', None))
                
                # 归一化
                if TORCH_AVAILABLE and torch:
                    vectors_tensor = torch.from_numpy(vectors)
                    vectors_tensor = F.normalize(vectors_tensor, p=2, dim=1)
                    vectors = vectors_tensor.numpy()
                else:
                    norms = np.linalg.norm(vectors, axis=1, keepdims=True)  # type: ignore
                    # 避免除以 0
                    if isinstance(norms, (int, float)):
                        norms = 1.0 if norms == 0 else norms
                    vectors = vectors / norms  # type: ignore
                
                self.index.add(vectors)
                
                start_id = self.next_id
                for i, (external_id, meta) in enumerate(zip(ids, metadata or [{}] * len(ids))):
                    internal_id = start_id + i
                    self.id_mapping[str(internal_id)] = external_id
                    self.reverse_mapping[external_id] = str(internal_id)
                    self.metadata[external_id] = meta or {}
                
                self.next_id += len(ids)
                logger.info(f"✅ 成功添加 {len(vectors)} 个向量")
                self.executor.submit(self._save_data)
                
            except Exception as e:
                logger.error(f"添加向量失败: {e}")
                raise
    
    def search(self, query_vectors: Any, k: int = 10, filter_metadata: Dict[str, Any] = None) -> List[List[Dict[str, Any]]]:
        """向量搜索"""
        if len(query_vectors) == 0:
            return [[] for _ in query_vectors]
        
        try:
            if not isinstance(query_vectors, np.ndarray):
                query_vectors = np.array(query_vectors, dtype=getattr(np, 'float32', None))
            elif query_vectors.dtype != getattr(np, 'float32', None):
                query_vectors = query_vectors.astype(getattr(np, 'float32', None))
            
            # 归一化查询向量
            if TORCH_AVAILABLE and torch:
                query_tensor = torch.from_numpy(query_vectors)
                if query_tensor.dim() == 1:
                    query_tensor = query_tensor.unsqueeze(0)
                query_tensor = F.normalize(query_tensor, p=2, dim=1)
                query_vectors = query_tensor.numpy()
            else:
                if getattr(query_vectors, 'ndim', 2) == 1:
                    query_vectors = query_vectors.reshape(1, -1)
                norms = np.linalg.norm(query_vectors, axis=1, keepdims=True)  # type: ignore
                query_vectors = query_vectors / norms  # type: ignore
            
            # 执行搜索 (使用 Any 屏蔽参数数量检查)
            idx_any: Any = self.index
            distances, indices = idx_any.search(query_vectors, k * 2)
            
            results = []
            for i, (dists, idxs) in enumerate(zip(distances, indices)):
                batch_result = []
                for dist, idx in zip(dists, idxs):
                    if idx == -1: continue
                    
                    internal_id = str(idx)
                    external_id = self.id_mapping.get(internal_id)
                    if not external_id: continue
                    
                    meta = self.metadata.get(external_id, {})
                    if filter_metadata and not self._match_filter(meta, filter_metadata):
                        continue
                    
                    batch_result.append({
                        'id': external_id,
                        'score': float(dist),
                        'metadata': meta,
                        'distance': dist
                    })
                
                batch_result.sort(key=lambda x: x['score'], reverse=True)
                results.append(batch_result[:k])
            
            return results
            
        except Exception as e:
            logger.error(f"向量搜索失败: {e}")
            return [[] for _ in query_vectors]
    
    def delete_vectors(self, ids: List[str]):
        """删除向量"""
        with self.lock:
            try:
                for external_id in ids:
                    internal_id = self.reverse_mapping.get(external_id)
                    if internal_id:
                        self.id_mapping.pop(internal_id, None)
                        self.reverse_mapping.pop(external_id, None)
                        self.metadata.pop(external_id, None)
                
                self._rebuild_index()
                logger.info(f"✅ 成功删除 {len(ids)} 个向量")
                self.executor.submit(self._save_data)
            except Exception as e:
                logger.error(f"删除向量失败: {e}")
                raise
    
    def update_vectors(self, ids: List[str], new_vectors: Any, new_metadata: List[Dict[str, Any]] = None):
        """更新向量"""
        self.delete_vectors(ids)
        self.add_vectors(new_vectors, ids, new_metadata or [{}] * len(ids))
    
    def get_stats(self) -> Dict[str, Any]:
        """获取索引统计信息"""
        with self.lock:
            total_vectors = len(self.id_mapping)
            stats = {
                'total_vectors': total_vectors,
                'index_type': self.config.index_type,
                'dimension': self.config.dimension,
                'storage_path': str(self.storage_path),
                'faiss_available': FAISS_AVAILABLE,
                'gpu_available': self.config.use_gpu and faiss and faiss.get_num_gpus() > 0 if FAISS_AVAILABLE and faiss else False
            }
            if hasattr(self.index, 'ntotal'):
                stats['faiss_total'] = self.index.ntotal
            return stats
    
    def _match_filter(self, metadata: Dict[str, Any], filter_dict: Dict[str, Any]) -> bool:
        """检查元数据是否匹配过滤条件"""
        for key, value in filter_dict.items():
            if metadata.get(key) != value:
                return False
        return True
    
    def _rebuild_index(self):
        """重建索引 (占位实现)"""
        pass
    
    def _save_data(self):
        """保存数据到磁盘"""
        try:
            if FAISS_AVAILABLE and faiss and self.index is not None:
                faiss.write_index(self.index, str(self.index_path))
            
            with open(self.metadata_path, 'wb') as f:
                pickle.dump(self.metadata, f)
            
            with open(self.mapping_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'id_mapping': self.id_mapping,
                    'reverse_mapping': self.reverse_mapping,
                    'next_id': self.next_id
                }, f, ensure_ascii=False, indent=2)
            logger.info("✅ 向量存储数据保存成功")
        except Exception as e:
            logger.error(f"保存数据失败: {e}")
    
    def _load_data(self):
        """从磁盘加载数据"""
        try:
            if self.index_path.exists() and FAISS_AVAILABLE and faiss:
                self.index = faiss.read_index(str(self.index_path))
            
            if self.metadata_path.exists():
                with open(self.metadata_path, 'rb') as f:
                    self.metadata = pickle.load(f)
            
            if self.mapping_path.exists():
                with open(self.mapping_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.id_mapping = data.get('id_mapping', {})
                    self.reverse_mapping = data.get('reverse_mapping', {})
                    self.next_id = data.get('next_id', 0)
        except Exception as e:
            logger.error(f"加载数据失败: {e}")
    
    def __del__(self):
        """析构函数"""
        try:
            self.executor.shutdown(wait=True)
            self._save_data()
        except:
            pass

class MockFAISSIndex:
    """FAISS索引的模拟实现"""
    
    def __init__(self, dimension: int):
        self.dimension = dimension
        self.vectors: List[Any] = []
        self.ntotal = 0
    
    def add(self, vectors: Any):
        if hasattr(vectors, 'data'):
            for vec in vectors.data:
                self.vectors.append(vec)
        else:
            for vec in vectors:
                self.vectors.append(vec.copy())
        self.ntotal = len(self.vectors)
    
    def search(self, query_vectors: Any, k: int):
        if not self.vectors:
            return np.zeros((len(query_vectors), k)), np.full((len(query_vectors), k), -1)
        
        vectors_matrix = np.array(self.vectors)
        distances = np.zeros((len(query_vectors), k))
        indices = np.zeros((len(query_vectors), k), dtype=int)
        
        for i, query in enumerate(query_vectors):
            norms = np.linalg.norm(vectors_matrix, axis=1) * np.linalg.norm(query)  # type: ignore
            similarities = np.dot(vectors_matrix, query) / (norms + 1e-8)  # type: ignore
            top_indices = np.argsort(similarities)[-k:][::-1]  # type: ignore
            distances[i] = similarities[top_indices]  # type: ignore
            indices[i] = top_indices
        return distances, indices

if __name__ == "__main__":
    config = VectorConfig(dimension=768, index_type="HNSW")
    store = FAISSVectorStore(config)
    
    f32 = getattr(np, 'float32', None)
    test_vectors = np.random.randn(100, 768).astype(f32)
    test_ids = [f"doc_{i}" for i in range(100)]
    test_metadata = [{"title": f"文档{i}", "category": "test"} for i in range(100)]
    
    store.add_vectors(test_vectors, test_ids, test_metadata)
    
    query_vector = np.random.randn(768).astype(f32)
    results = store.search(query_vector.reshape(1, -1), k=5)
    
    print(f"总向量数: {store.get_stats()['total_vectors']}")
    for i, result in enumerate(results[0]):
        print(f"{i+1}. ID: {result['id']}, 相似度: {result['score']:.4f}")
        