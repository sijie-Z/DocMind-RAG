"""Hardware detection — auto-selects model tier based on GPU/CPU/RAM."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class HardwareProfile:
    tier: str          # "cpu" | "gpu-8g" | "gpu-16g"
    gpu_name: str      # e.g. "NVIDIA GeForce RTX 4060"
    vram_mb: int       # 0 if no GPU
    embedding_model: str
    reranker_model: str
    vector_dim: int


def _detect_nvidia_gpu() -> tuple[str, int]:
    """Return (gpu_name, vram_mb) or ("", 0) if no NVIDIA GPU."""
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return "", 0
    try:
        out = subprocess.check_output(
            [nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            timeout=5, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        if not out:
            return "", 0
        parts = out.split(",")
        name = parts[0].strip()
        vram = int(parts[1].strip().split()[0]) if len(parts) > 1 else 0
        return name, vram
    except Exception:
        return "", 0


def _try_torch_cuda() -> tuple[str, int]:
    """Fallback: detect GPU via PyTorch if available."""
    try:
        import torch  # noqa: F401
    except ImportError:
        return "", 0
    try:
        name = torch.cuda.get_device_name(0)
        vram = torch.cuda.get_device_properties(0).total_mem // (1024 * 1024)
        return name, vram
    except Exception:
        return "", 0


def detect() -> HardwareProfile:
    """Detect hardware and return the recommended model tier."""
    gpu_name, vram_mb = _detect_nvidia_gpu()
    if not gpu_name:
        gpu_name, vram_mb = _try_torch_cuda()

    if vram_mb >= 14000:
        tier = "gpu-16g"
        embedding = os.environ.get("EMBEDDING_MODEL", "Qwen3-Embedding-8B")
        reranker = os.environ.get("LOCAL_RERANKER_MODEL", "Qwen3-Reranker-8B")
        vector_dim = int(os.environ.get("VECTOR_DIMENSION", "4096"))
    elif vram_mb >= 6000:
        tier = "gpu-8g"
        embedding = os.environ.get("EMBEDDING_MODEL", "Qwen3-Embedding-4B")
        reranker = os.environ.get("LOCAL_RERANKER_MODEL", "Qwen3-Reranker-4B")
        vector_dim = int(os.environ.get("VECTOR_DIMENSION", "2048"))
    else:
        tier = "cpu"
        embedding = os.environ.get("EMBEDDING_MODEL", "bge-m3")
        reranker = os.environ.get("LOCAL_RERANKER_MODEL", "BAAI/bge-reranker-base")
        vector_dim = int(os.environ.get("VECTOR_DIMENSION", "1024"))

    profile = HardwareProfile(
        tier=tier, gpu_name=gpu_name, vram_mb=vram_mb,
        embedding_model=embedding, reranker_model=reranker,
        vector_dim=vector_dim,
    )
    logger.info(
        "Hardware: tier=%s gpu=%s vram=%dMB → emb=%s rerank=%s dim=%d",
        tier, gpu_name or "none", vram_mb, embedding, reranker, vector_dim,
    )
    return profile
